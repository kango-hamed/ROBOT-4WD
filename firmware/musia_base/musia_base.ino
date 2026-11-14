// =====================================================================
// musia_base — firmware de la carte moteur MUSIA v2
// Arduino UNO + shield moteur Keyestudio (2 voies : côté gauche, côté droit)
//
// Rôle unique : appliquer les consignes PWM envoyées par le Raspberry Pi
// et couper les moteurs si le Pi se tait. Toute la décision est sur le Pi.
//
// Protocole (identique à ros2_ws/src/musia_base/musia_base/protocol.py) :
//   ligne ASCII "<corps>*<XX>\n", XX = OU exclusif des octets du corps
//   Pi -> Arduino : "M <g> <d>" (-255..255)  |  "S" (arrêt)  |  "P" (ping)
//   Arduino -> Pi : "H musia_base 2.0"  |  "T <ms> <g> <d> <0|1>"  |  "E <code>"
//
// Sécurités :
//   * chien de garde : sans consigne valide depuis 400 ms, moteurs coupés ;
//   * rampe : le PWM varie d'au plus RAMPE_PAS toutes les 10 ms, ce qui limite
//     les pointes de courant au démarrage (et les chutes de tension).
//
// Brochage repris de l'ancien sketch Keyestudio (code_arduino.ino) :
//   gauche : DIR 2, PWM 5     droite : DIR 4, PWM 6
//   Convention du shield : en marche avant (DIR = HIGH), le PWM est inversé
//   (vitesse v -> analogWrite(255 - v)) ; en marche arrière, analogWrite(v).
// =====================================================================

#include <Arduino.h>

const char NOM[] = "musia_base";
const char VERSION[] = "2.0";

const uint8_t PIN_G_DIR = 2, PIN_G_PWM = 5;
const uint8_t PIN_D_DIR = 4, PIN_D_PWM = 6;

// Mettre à 1 si un côté tourne à l'envers une fois le robot monté.
#define INVERSER_GAUCHE 0
#define INVERSER_DROITE 0

const unsigned long CHIEN_DE_GARDE_MS = 400;
const unsigned long PERIODE_RAMPE_MS = 10;
const int RAMPE_PAS = 12;                  // 0 -> 255 en ~210 ms
const unsigned long PERIODE_TELEMETRIE_MS = 100;

const uint8_t TAILLE_LIGNE = 32;
char ligne[TAILLE_LIGNE];
uint8_t longueur = 0;
bool debordement = false;

int consigneG = 0, consigneD = 0;          // demandé par le Pi
int appliqueG = 0, appliqueD = 0;          // réellement envoyé aux moteurs
unsigned long derniereConsigne = 0;
bool chienDeGarde = true;                  // moteurs coupés tant qu'aucune consigne n'est arrivée
unsigned long derniereRampe = 0, derniereTelemetrie = 0;

// ---------------------------------------------------------------------
// Trames sortantes
// ---------------------------------------------------------------------

uint8_t sommeXor(const char *s, uint8_t n) {
  uint8_t x = 0;
  for (uint8_t i = 0; i < n; i++) x ^= (uint8_t)s[i];
  return x;
}

void envoyer(const char *corps) {
  uint8_t n = strlen(corps);
  char cs[3];
  snprintf(cs, sizeof(cs), "%02X", sommeXor(corps, n));
  Serial.print(corps);
  Serial.print('*');
  Serial.println(cs);                      // println ajoute "\r\n" : le Pi ignore le '\r'
}

void envoyerBonjour() {
  char t[32];
  snprintf(t, sizeof(t), "H %s %s", NOM, VERSION);
  envoyer(t);
}

void envoyerErreur(uint8_t code) {
  char t[8];
  snprintf(t, sizeof(t), "E %u", code);
  envoyer(t);
}

void envoyerTelemetrie() {
  char t[40];
  snprintf(t, sizeof(t), "T %lu %d %d %d", millis(), appliqueG, appliqueD, chienDeGarde ? 1 : 0);
  envoyer(t);
}

// ---------------------------------------------------------------------
// Moteurs
// ---------------------------------------------------------------------

void ecrireMoteur(uint8_t pinDir, uint8_t pinPwm, int pwm, bool inverser) {
  if (inverser) pwm = -pwm;
  if (pwm > 0) {
    digitalWrite(pinDir, HIGH);
    analogWrite(pinPwm, 255 - pwm);
  } else if (pwm < 0) {
    digitalWrite(pinDir, LOW);
    analogWrite(pinPwm, -pwm);
  } else {
    digitalWrite(pinDir, LOW);
    analogWrite(pinPwm, 0);
  }
}

int rapprocher(int actuel, int cible) {
  if (cible > actuel) return min(cible, actuel + RAMPE_PAS);
  if (cible < actuel) return max(cible, actuel - RAMPE_PAS);
  return actuel;
}

void appliquerRampe() {
  int cibleG = chienDeGarde ? 0 : consigneG;
  int cibleD = chienDeGarde ? 0 : consigneD;
  // un changement de sens passe par zéro : pas d'inversion brutale
  if ((appliqueG > 0 && cibleG < 0) || (appliqueG < 0 && cibleG > 0)) cibleG = 0;
  if ((appliqueD > 0 && cibleD < 0) || (appliqueD < 0 && cibleD > 0)) cibleD = 0;
  appliqueG = rapprocher(appliqueG, cibleG);
  appliqueD = rapprocher(appliqueD, cibleD);
  ecrireMoteur(PIN_G_DIR, PIN_G_PWM, appliqueG, INVERSER_GAUCHE);
  ecrireMoteur(PIN_D_DIR, PIN_D_PWM, appliqueD, INVERSER_DROITE);
}

void arretImmediat() {
  consigneG = consigneD = appliqueG = appliqueD = 0;
  ecrireMoteur(PIN_G_DIR, PIN_G_PWM, 0, false);
  ecrireMoteur(PIN_D_DIR, PIN_D_PWM, 0, false);
}

// ---------------------------------------------------------------------
// Trames entrantes
// ---------------------------------------------------------------------

int valeurHex(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

void traiterLigne(char *s, uint8_t n) {
  char *etoile = strrchr(s, '*');
  if (!etoile || etoile == s || (s + n) - etoile != 3) { envoyerErreur(1); return; }
  int h = valeurHex(etoile[1]), l = valeurHex(etoile[2]);
  uint8_t nCorps = etoile - s;
  if (h < 0 || l < 0 || (uint8_t)(h * 16 + l) != sommeXor(s, nCorps)) { envoyerErreur(2); return; }
  *etoile = '\0';

  if (s[0] == 'M' && s[1] == ' ') {
    char *fin;
    long g = strtol(s + 2, &fin, 10);
    if (fin == s + 2 || *fin != ' ') { envoyerErreur(1); return; }
    char *debut = fin + 1;
    long d = strtol(debut, &fin, 10);
    if (fin == debut || *fin != '\0') { envoyerErreur(1); return; }
    consigneG = constrain(g, -255, 255);
    consigneD = constrain(d, -255, 255);
    derniereConsigne = millis();
    chienDeGarde = false;
  } else if (s[0] == 'S' && s[1] == '\0') {
    arretImmediat();
    derniereConsigne = millis();           // un arrêt volontaire n'est pas une perte de liaison
    chienDeGarde = false;
  } else if (s[0] == 'P' && s[1] == '\0') {
    envoyerBonjour();
  } else {
    envoyerErreur(3);
  }
}

void lireSerie() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      if (!debordement && longueur > 0) {
        ligne[longueur] = '\0';
        traiterLigne(ligne, longueur);
      } else if (debordement) {
        envoyerErreur(1);
      }
      longueur = 0;
      debordement = false;
    } else if (longueur < TAILLE_LIGNE - 1) {
      ligne[longueur++] = c;
    } else {
      debordement = true;
    }
  }
}

// ---------------------------------------------------------------------

void setup() {
  pinMode(PIN_G_DIR, OUTPUT);
  pinMode(PIN_G_PWM, OUTPUT);
  pinMode(PIN_D_DIR, OUTPUT);
  pinMode(PIN_D_PWM, OUTPUT);
  arretImmediat();
  Serial.begin(115200);
  envoyerBonjour();
}

void loop() {
  lireSerie();
  unsigned long maintenant = millis();
  if (!chienDeGarde && maintenant - derniereConsigne > CHIEN_DE_GARDE_MS) {
    chienDeGarde = true;
    consigneG = consigneD = 0;
  }
  if (maintenant - derniereRampe >= PERIODE_RAMPE_MS) {
    derniereRampe = maintenant;
    appliquerRampe();
  }
  if (maintenant - derniereTelemetrie >= PERIODE_TELEMETRIE_MS) {
    derniereTelemetrie = maintenant;
    envoyerTelemetrie();
  }
}
