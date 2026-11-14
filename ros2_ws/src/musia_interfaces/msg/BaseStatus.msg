# État de la carte moteur (Arduino), publié par base_node.
std_msgs/Header header
bool connected          # trames reçues depuis moins d'une seconde
bool watchdog_tripped   # l'Arduino a coupé les moteurs faute de commande
int16 pwm_left          # -255..255, valeur réellement appliquée
int16 pwm_right
string firmware
