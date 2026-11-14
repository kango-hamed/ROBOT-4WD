# Visiteur vu par la webcam (ou un autre détecteur).
std_msgs/Header header
bool detected
float32 distance_m      # estimation, NaN si inconnue
float32 bearing_rad     # direction dans le repère base_link (+ = à gauche)
float32 confidence      # 0..1
