# État de la visite, publié par tour_node (pour l'admin et le débogage).
std_msgs/Header header
string phase            # VEILLE, ACCUEIL, GUIDAGE, PRESENTATION, ECOUTE, REPONSE, RETOUR
string stop_id          # arrêt courant ('' hors visite)
uint8 stop_index
uint8 stop_count
bool online             # le serveur de conversation répond
