/*
 * MILLIE — ESP32 CYD (ESP32-2432S028R)
 * WiFi promiscuous sniffer + BLE scanner (time-sliced) + CYD display,
 * streaming a FULL firehose of devices over USB serial to the phone.
 *
 * The phone is the brain: it holds the single device table, computes the
 * 4 views (Clusters / BLE / All / Flock), does GPS + Flock classification,
 * and sends flock verdicts back down as DET| lines.
 *
 * TIME-SLICING: one radio can't do WiFi-promiscuous and BLE at once, so we
 * alternate windows:  WIFI_WINDOW_MS sniffing WiFi (hopping channels),
 * then BLE_WINDOW_MS scanning BLE. Both coverages drop a bit; that's the
 * single-radio cost you accepted.
 *
 * UP  (ESP->phone), one JSON per line:
 *   {"t":"wifi","mac":..,"rssi":..,"ch":..,"ftype":..,"ssid":..,"oui_flock":bool}
 *   {"t":"ble","mac":..,"rssi":..,"name":..,"tracker":".."}
 *   {"_status":"alive","radio":"wifi|ble","ch":..,"wifi":N,"ble":M}
 * DOWN(phone->ESP):
 *   DET|mac|conf|bearing|dist|rssi
 *
 * Libs: TFT_eSPI (CYD config as TC_OS). BLE via NimBLE-Arduino
 *   arduino-cli lib install "NimBLE-Arduino"
 */
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include <TFT_eSPI.h>
#include <NimBLEDevice.h>
#include <math.h>

// ---- Display (CYD, TC_OS wiring) ----
#define PIN_BL 27
TFT_eSPI tft = TFT_eSPI();
#define SCR_W 240
#define SCR_H 320
uint16_t C_GREEN,C_DIM,C_AMBER,C_RED,C_BG,C_GREY,C_CYAN,C_BLUE;

// ---- Time-slicing windows ----
#define WIFI_WINDOW_MS 6000
#define BLE_WINDOW_MS  3000
#define HOP_INTERVAL_MS 220
#define MAX_CHANNEL 13
#define DEDUP_WINDOW_MS 4000
#define BAUD 115200

// ---- Flock OUI pre-filter (phone makes the real call) ----
const uint8_t FLOCK_OUIS[][3] = {
  {0xD4,0xAD,0xFC},{0xAC,0x67,0xB2},{0x84,0xF3,0xEB},{0xB4,0xE6,0x2D},
  {0xCC,0xDB,0xA7},{0x24,0x0A,0xC4},{0x30,0xAE,0xA4},{0x94,0xB9,0x7E},
  {0xA4,0xCF,0x12},{0xC0,0x49,0xEF},
};
const int FLOCK_OUI_COUNT=sizeof(FLOCK_OUIS)/3;

#define DEDUP_SLOTS 64
struct Seen{uint8_t mac[6];uint32_t t;};
Seen seen[DEDUP_SLOTS];
int seenIdx=0;
volatile uint32_t wifiHits=0, bleHits=0, txLines=0;
uint8_t curChannel=1;
bool radioWifi=true;                 // current slice
uint32_t sliceStart=0;

// ---- Display device store (compact; phone holds the full table) ----
#define MAX_DET 20
struct Det{char mac[18];char kind;char conf;int bearing;float dist;int rssi;uint32_t lastSeen;bool used;};
// kind: 'W' wifi, 'B' ble ; conf: 'H''M''L''?'
Det dets[MAX_DET];
float radarScaleM=150.0;
bool logChanged=false;
char inLine[128]; int inPos=0;
uint32_t lastDetFromPhone=0;

// counts for the header
int cntFlock=0,cntBle=0,cntAll=0;

// ================= helpers =================
bool ouiMatches(const uint8_t*mac){
  for(int i=0;i<FLOCK_OUI_COUNT;i++)
    if(mac[0]==FLOCK_OUIS[i][0]&&mac[1]==FLOCK_OUIS[i][1]&&mac[2]==FLOCK_OUIS[i][2])return true;
  return false;
}
bool recentlySeen(const uint8_t*mac){
  uint32_t now=millis();
  for(int i=0;i<DEDUP_SLOTS;i++)
    if(memcmp(seen[i].mac,mac,6)==0&&(now-seen[i].t)<DEDUP_WINDOW_MS)return true;
  memcpy(seen[seenIdx].mac,mac,6);seen[seenIdx].t=now;seenIdx=(seenIdx+1)%DEDUP_SLOTS;
  return false;
}
void upsert(const char*mac,char kind,int rssi,char conf){
  for(int i=0;i<MAX_DET;i++)
    if(dets[i].used&&strcmp(dets[i].mac,mac)==0){
      dets[i].rssi=rssi;dets[i].lastSeen=millis();
      if(conf!='?'&&dets[i].conf=='?')dets[i].conf=conf;
      return;
    }
  int slot=-1;uint32_t oldest=0xFFFFFFFF;
  for(int i=0;i<MAX_DET;i++){if(!dets[i].used){slot=i;break;}if(dets[i].lastSeen<oldest){oldest=dets[i].lastSeen;slot=i;}}
  strncpy(dets[slot].mac,mac,17);dets[slot].mac[17]=0;
  dets[slot].kind=kind;dets[slot].conf=conf;dets[slot].bearing=-1;dets[slot].dist=-1;
  dets[slot].rssi=rssi;dets[slot].lastSeen=millis();dets[slot].used=true;
  logChanged=true;
}

// ================= WiFi sniffer =================
const char* frameTypeName(uint8_t fc0){
  uint8_t type=(fc0>>2)&0x03,subtype=(fc0>>4)&0x0F;
  if(type==0&&subtype==4)return "probe_req";
  if(type==0&&subtype==5)return "probe_resp";
  if(type==0&&subtype==8)return "beacon";
  if(type==2)return "data";
  return "mgmt";
}
int extractSSID(const uint8_t*payload,int len,char*out){
  int p=24;
  while(p+2<=len){
    uint8_t id=payload[p],ln=payload[p+1];
    if(id==0){int n=ln>22?22:ln;if(p+2+n>len)n=len-(p+2);
      for(int i=0;i<n;i++){char c=payload[p+2+i];out[i]=(c>=32&&c<127)?c:'?';}
      out[n]=0;return n;}
    p+=2+ln;
  }
  out[0]=0;return 0;
}
void wifiCB(void*buf,wifi_promiscuous_pkt_type_t type){
  const wifi_promiscuous_pkt_t*pkt=(wifi_promiscuous_pkt_t*)buf;
  const uint8_t*payload=pkt->payload;
  int len=pkt->rx_ctrl.sig_len;
  if(len<24)return;
  uint8_t fc0=payload[0];
  const uint8_t*src=payload+10;
  if(recentlySeen(src))return;      // full firehose, but still de-spam
  char ssid[33];const char*ft=frameTypeName(fc0);
  if(!strcmp(ft,"probe_req")||!strcmp(ft,"beacon")||!strcmp(ft,"probe_resp"))extractSSID(payload,len,ssid);
  else ssid[0]=0;
  wifiHits++;
  bool flock=ouiMatches(src);
  char macstr[18];
  snprintf(macstr,sizeof(macstr),"%02X:%02X:%02X:%02X:%02X:%02X",src[0],src[1],src[2],src[3],src[4],src[5]);
  upsert(macstr,'W',pkt->rx_ctrl.rssi, flock?'?':'L');
  Serial.printf("{\"t\":\"wifi\",\"mac\":\"%s\",\"rssi\":%d,\"ch\":%d,\"ftype\":\"%s\",\"ssid\":\"%s\",\"oui_flock\":%s}\n",
    macstr,pkt->rx_ctrl.rssi,curChannel,ft,ssid, flock?"true":"false");
  txLines++;
}

// ================= BLE scanner =================
NimBLEScan* bleScan=nullptr;
const char* bleTrackerGuess(const std::string& name){
  std::string n=name; for(auto&c:n)c=tolower(c);
  if(n.find("airtag")!=std::string::npos)return "airtag";
  if(n.find("tile")!=std::string::npos)return "tile";
  if(n.find("smarttag")!=std::string::npos)return "smarttag";
  if(n.find("flipper")!=std::string::npos)return "flipper";
  return "";
}
class ScanCB: public NimBLEScanCallbacks {
  void onResult(const NimBLEAdvertisedDevice* dev) override {
    bleHits++;
    std::string mac=dev->getAddress().toString();
    std::string name=dev->haveName()?dev->getName():"";
    int rssi=dev->getRSSI();
    const char* tr=bleTrackerGuess(name);
    upsert(mac.c_str(),'B',rssi,'L');
    Serial.printf("{\"t\":\"ble\",\"mac\":\"%s\",\"rssi\":%d,\"name\":\"%s\",\"tracker\":\"%s\"}\n",
      mac.c_str(),rssi,name.c_str(),tr);
    txLines++;
  }
};
ScanCB scanCB;

// ================= phone -> ESP verdict =================
char confChar(const char*c){
  if(!strcmp(c,"HIGH")||!strcmp(c,"H"))return 'H';
  if(!strcmp(c,"MEDIUM")||!strcmp(c,"M"))return 'M';
  if(!strcmp(c,"LOW")||!strcmp(c,"L"))return 'L';
  return '?';
}
int splitPipes(char*line,char*f[],int mx){int n=0;char*p=line;f[n++]=p;for(;*p&&n<mx;p++)if(*p=='|'){*p=0;f[n++]=p+1;}return n;}
void applyDET(char*line){
  char*f[6];int nf=splitPipes(line,f,6);
  if(nf<2||strcmp(f[0],"DET"))return;
  lastDetFromPhone=millis();
  char*mac=f[1];char*conf=nf>2?f[2]:(char*)"";char*brg=nf>3?f[3]:(char*)"";
  char*dist=nf>4?f[4]:(char*)"";char*rssi=nf>5?f[5]:(char*)"";
  for(int i=0;i<MAX_DET;i++)if(dets[i].used&&strcmp(dets[i].mac,mac)==0){
    if(*conf)dets[i].conf=confChar(conf);
    dets[i].bearing=(*brg)?atoi(brg):-1;
    dets[i].dist=(*dist)?atof(dist):-1;
    if(*rssi)dets[i].rssi=atoi(rssi);
    dets[i].lastSeen=millis();logChanged=true;return;
  }
}
void pumpSerialIn(){
  while(Serial.available()){
    char c=Serial.read();
    if(c=='\n'||c=='\r'){if(inPos>0){inLine[inPos]=0;applyDET(inLine);inPos=0;}}
    else if(inPos<(int)sizeof(inLine)-1)inLine[inPos++]=c;
  }
}

// ================= Display =================
uint16_t confColor(char c){if(c=='H')return C_RED;if(c=='M')return C_AMBER;if(c=='L')return C_GREEN;return C_GREY;}
uint16_t kindColor(char k){return k=='B'?C_BLUE:C_GREEN;}
float rssiToDist(int rssi){float d=powf(10.0f,((-45.0f-(float)rssi)/30.0f));if(d<0.5f)d=0.5f;if(d>2000)d=2000;return d;}
#define RAD_CX 120
#define RAD_CY 122
#define RAD_R  104
#define LOG_Y0 232
int prevSweep=0;
void drawRadarStatic(){
  for(int i=1;i<=4;i++)tft.drawCircle(RAD_CX,RAD_CY,RAD_R*i/4,C_DIM);
  tft.drawFastVLine(RAD_CX,RAD_CY-RAD_R,RAD_R*2,C_DIM);
  tft.drawFastHLine(RAD_CX-RAD_R,RAD_CY,RAD_R*2,C_DIM);
  tft.fillCircle(RAD_CX,RAD_CY,3,C_GREEN);
  tft.setTextColor(C_GREEN,C_BG);tft.setTextSize(1);
  tft.setCursor(RAD_CX-3,RAD_CY-RAD_R-9);tft.print("N");
}
void plotBlips(){
  float maxD=60;
  for(int i=0;i<MAX_DET;i++)if(dets[i].used){float d=(dets[i].dist>0)?dets[i].dist:rssiToDist(dets[i].rssi);if(d>maxD)maxD=d;}
  radarScaleM=ceilf(maxD*1.2f/50.0f)*50.0f;if(radarScaleM<50)radarScaleM=50;
  for(int i=0;i<MAX_DET;i++){
    if(!dets[i].used)continue;
    uint16_t col=(dets[i].conf=='H'||dets[i].conf=='M')?confColor(dets[i].conf):kindColor(dets[i].kind);
    if(dets[i].bearing>=0&&dets[i].dist>0){
      float r=fminf(dets[i].dist/radarScaleM*RAD_R,RAD_R),a=dets[i].bearing*PI/180.0;
      int px=RAD_CX+(int)(r*sinf(a)),py=RAD_CY-(int)(r*cosf(a));
      tft.fillCircle(px,py,4,col);tft.drawCircle(px,py,7,col);
    }else{float d=(dets[i].dist>0)?dets[i].dist:rssiToDist(dets[i].rssi);float r=fminf(d/radarScaleM*RAD_R,RAD_R);
      // BLE = dashed-ish (draw 2 arcs), wifi = full ring
      tft.drawCircle(RAD_CX,RAD_CY,(int)r,col);
    }
  }
}
void drawSweep(){
  float ap=(prevSweep-90)*PI/180.0;
  tft.drawLine(RAD_CX,RAD_CY,RAD_CX+(int)(RAD_R*cosf(ap)),RAD_CY+(int)(RAD_R*sinf(ap)),C_BG);
  drawRadarStatic();plotBlips();
  prevSweep=(prevSweep+6)%360;
  float a=(prevSweep-90)*PI/180.0;
  tft.drawLine(RAD_CX,RAD_CY,RAD_CX+(int)(RAD_R*cosf(a)),RAD_CY+(int)(RAD_R*sinf(a)),C_GREEN);
}
void recount(){
  cntFlock=cntBle=cntAll=0;
  for(int i=0;i<MAX_DET;i++)if(dets[i].used){cntAll++;if(dets[i].kind=='B')cntBle++;if(dets[i].conf=='H'||dets[i].conf=='M')cntFlock++;}
}
void drawLog(){
  recount();
  tft.fillRect(0,LOG_Y0-4,SCR_W,SCR_H-(LOG_Y0-4),C_BG);
  tft.drawFastHLine(0,LOG_Y0-4,SCR_W,C_DIM);
  tft.setTextSize(1);
  tft.setTextColor(C_RED,C_BG);tft.setCursor(4,LOG_Y0-1);tft.printf("FLK:%d",cntFlock);
  tft.setTextColor(C_BLUE,C_BG);tft.setCursor(58,LOG_Y0-1);tft.printf("BLE:%d",cntBle);
  tft.setTextColor(C_GREEN,C_BG);tft.setCursor(112,LOG_Y0-1);tft.printf("ALL:%d",cntAll);
  int order[MAX_DET],n=0;
  for(int i=0;i<MAX_DET;i++)if(dets[i].used)order[n++]=i;
  for(int a=0;a<n;a++)for(int b=a+1;b<n;b++)if(dets[order[b]].lastSeen>dets[order[a]].lastSeen){int t=order[a];order[a]=order[b];order[b]=t;}
  int y=LOG_Y0+10;
  for(int k=0;k<n&&y<SCR_H-8;k++){
    Det&d=dets[order[k]];
    uint16_t col=(d.conf=='H'||d.conf=='M')?confColor(d.conf):kindColor(d.kind);
    const char*tag=d.kind=='B'?"BLE":(d.conf=='H'?"FLK":d.conf=='M'?"flk":"wifi");
    tft.setTextColor(col,C_BG);tft.setCursor(4,y);tft.printf("%s %s %d",tag,d.mac+9,d.rssi);
    y+=11;
  }
  if(n==0){tft.setTextColor(C_DIM,C_BG);tft.setCursor(4,LOG_Y0+10);tft.print("scanning...");}
}
void drawHeader(){
  tft.fillRect(0,0,SCR_W,22,C_BG);
  tft.setTextColor(C_GREEN,C_BG);tft.setTextSize(1);tft.setCursor(2,3);tft.print("MILLIE");
  tft.setTextColor(radioWifi?C_CYAN:C_BLUE,C_BG);tft.setCursor(84,3);
  if(radioWifi)tft.printf("WIFI CH%02d",curChannel); else tft.print("BLE SCAN");
  // Proves the ESP itself is alive + transmitting, independent of the phone.
  tft.setTextColor(C_AMBER,C_BG);tft.setCursor(2,13);tft.printf("TX:%lu",(unsigned long)txLines);
  bool linked=(millis()-lastDetFromPhone)<15000 && lastDetFromPhone>0;
  tft.setTextColor(linked?C_GREEN:C_GREY,C_BG);tft.setCursor(70,13);
  tft.print(linked?"PHONE:LINKED":"PHONE:--");
}

// ================= Setup / Loop =================
void startWifiSlice(){
  radioWifi=true;
  esp_wifi_set_promiscuous(true);
  esp_wifi_set_promiscuous_rx_cb(&wifiCB);
  esp_wifi_set_channel(curChannel,WIFI_SECOND_CHAN_NONE);
}
void stopWifiSlice(){ esp_wifi_set_promiscuous(false); }

void startBleSlice(){
  radioWifi=false;
  if(!bleScan){
    NimBLEDevice::init("");
    bleScan=NimBLEDevice::getScan();
    bleScan->setScanCallbacks(&scanCB,false);
    bleScan->setActiveScan(true);
    bleScan->setInterval(45);
    bleScan->setWindow(35);
  }
  bleScan->start(0,false,true);   // non-blocking, run until we stop it
}
void stopBleSlice(){ if(bleScan)bleScan->stop(); }

void setup(){
  Serial.begin(BAUD);delay(300);
  esp_log_level_set("*",ESP_LOG_NONE);
  esp_err_t nv=nvs_flash_init();
  if(nv==ESP_ERR_NVS_NO_FREE_PAGES||nv==ESP_ERR_NVS_NEW_VERSION_FOUND){nvs_flash_erase();nvs_flash_init();}
  esp_event_loop_create_default();

  pinMode(PIN_BL,OUTPUT);digitalWrite(PIN_BL,HIGH);
  tft.init();tft.setRotation(0);
  C_BG=tft.color565(6,12,6);C_GREEN=tft.color565(57,255,20);C_DIM=tft.color565(31,110,18);
  C_AMBER=tft.color565(255,176,0);C_RED=tft.color565(255,49,49);C_GREY=tft.color565(120,120,120);
  C_CYAN=tft.color565(0,200,200);C_BLUE=tft.color565(80,140,255);
  tft.fillScreen(C_BG);drawHeader();drawRadarStatic();drawLog();

  // WiFi driver up (null mode) for promiscuous.
  wifi_init_config_t cfg=WIFI_INIT_CONFIG_DEFAULT();
  esp_wifi_init(&cfg);
  esp_wifi_set_storage(WIFI_STORAGE_RAM);
  esp_wifi_set_mode(WIFI_MODE_NULL);
  esp_wifi_start();delay(150);

  Serial.println("{\"_status\":\"MILLIE multi boot\"}");
  sliceStart=millis();
  startWifiSlice();
}
uint32_t lastHop=0,lastBeat=0,lastSweep=0,lastHdr=0;
void loop(){
  pumpSerialIn();
  uint32_t now=millis();

  // ---- time-slice scheduler ----
  if(radioWifi){
    if(now-lastHop>=HOP_INTERVAL_MS){lastHop=now;curChannel++;if(curChannel>MAX_CHANNEL)curChannel=1;esp_wifi_set_channel(curChannel,WIFI_SECOND_CHAN_NONE);}
    if(now-sliceStart>=WIFI_WINDOW_MS){ stopWifiSlice(); startBleSlice(); sliceStart=now; }
  } else {
    if(now-sliceStart>=BLE_WINDOW_MS){ stopBleSlice(); startWifiSlice(); sliceStart=now; }
  }

  if(now-lastSweep>=66){lastSweep=now;drawSweep();}
  if(now-lastHdr>=400){lastHdr=now;drawHeader();}
  if(logChanged){logChanged=false;drawLog();}
  for(int i=0;i<MAX_DET;i++)if(dets[i].used&&now-dets[i].lastSeen>30000){dets[i].used=false;logChanged=true;}
  if(now-lastBeat>3000){lastBeat=now;
    Serial.printf("{\"_status\":\"alive\",\"radio\":\"%s\",\"ch\":%d,\"wifi\":%lu,\"ble\":%lu}\n",
      radioWifi?"wifi":"ble",curChannel,(unsigned long)wifiHits,(unsigned long)bleHits);
  }
}
