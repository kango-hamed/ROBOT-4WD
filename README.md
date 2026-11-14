# MUSIA v2 — robot guide de musée

Refonte complète du logiciel de MUSIA (ancien dépôt : `kango-hamed/musia`), adaptée au nouveau châssis modulaire conçu dans `D:\work\sene-musia-cad`.

**Objectif actuel : un prototype qui fonctionne.** Il doit faire deux choses :
- aller seul d'un point A à un point B ;
- se piloter à distance depuis un téléphone, avec la vidéo de la webcam.

La visite guidée et la voix sont **en pause** : leur code est conservé, mais le prototype ne les lance pas.

**État au 14/11/2026.** Le code ROS 2, le firmware Arduino et la page de pilotage sont écrits. 86 tests tournent sans ROS. **Rien n'a encore tourné sur le Raspberry Pi** : la compilation `colcon`, les lancements et le firmware restent à vérifier sur le robot.

## Architecture du prototype

```
            Raspberry Pi 5 (Ubuntu 24.04, ROS 2 Jazzy)

 téléphone ──Wi-Fi──▶ page de pilotage (pilot_node, port 8080)
     ▲                  │ joystick        │ destination      │ STOP
     │                  ▼                 ▼                  ▼
     │           /cmd_vel_teleop     /goal_pose ─▶ Nav2   /musia/estop
     │                  │                 (slam_toolbox,       │
     │                  │                  planificateur,      │
     │                  │                  MPPI, anticollision)│
     │                  │                 │ /cmd_vel_auto      │
     │                  ▼                 ▼                    ▼
     │             twist_mux : arrêt d'urgence > pilote > navigation
     │                  │ /cmd_vel
     │                  ▼
     │             base_node ── USB série ──▶ Arduino UNO + shield ─▶ 4 moteurs TT
     │
     └── vidéo MJPEG (web_video_server, port 8081) ◀── webcam USB (v4l2_camera)

 lidar STL-19P ─▶ /scan ─▶ rf2o ─▶ EKF ◀── imu_node (gyroscope)      → /odom
 servo 9 g ◀── head_node ◀── /musia/head/target (curseur de la page)
 INA219 ─▶ battery_node ─▶ /musia/battery (affiché sur la page)
```

| Paquet | Rôle | Prototype |
|---|---|---|
| `firmware/musia_base` | Arduino : applique les PWM reçus, chien de garde de 400 ms, rampe d'accélération | oui |
| `musia_interfaces` | Messages, service `TourCommand`, action `Say` | oui |
| `musia_base` | `/cmd_vel` → protocole série → Arduino ; état de la carte moteur | oui |
| `musia_hardware` | Servo de la tête (PWM matériel du Pi), IMU MPU-6050, INA219 | oui |
| `musia_pilot` | Page de pilotage : joystick, carte en direct, destinations, points nommés, arrêt d'urgence, vidéo | oui |
| `musia_bringup` | URDF tiré de la CAO, paramètres, lancements | oui |
| `musia_tour` | Visite guidée : machine à états, fichier de visite YAML | en pause |
| `musia_voice` | Parole et écoute (ignoré par `colcon` : `COLCON_IGNORE`) | en pause |

## Mise en route, étape par étape

Chaque étape vérifie une pièce avant de passer à la suivante. Si une étape échoue, inutile d'aller plus loin.

### 1. Installation sur le Pi

```bash
# Ubuntu 24.04 + ROS 2 Jazzy installés
sudo apt install python3-vcstool python3-serial python3-smbus2 python3-yaml \
  ros-jazzy-navigation2 ros-jazzy-nav2-bringup ros-jazzy-robot-localization \
  ros-jazzy-slam-toolbox ros-jazzy-twist-mux ros-jazzy-v4l2-camera \
  ros-jazzy-web-video-server ros-jazzy-teleop-twist-keyboard
cd ros2_ws && vcs import src/externes < musia.repos
rosdep install --from-paths src --ignore-src -y
colcon build --symlink-install && source install/setup.bash
ls -l /dev/serial/by-id/      # reporter les chemins de l'Arduino et du lidar dans config/robot.yaml
```

- **Firmware** : ouvrir `firmware/musia_base/musia_base.ino` dans l'IDE Arduino et téléverser sur l'UNO. Aucune bibliothèque n'est nécessaire.
- **Servo** : le PWM matériel du Pi 5 s'active par une ligne `dtoverlay=pwm…` dans `/boot/firmware/config.txt`. Le numéro de `pwmchip` et le canal de GPIO 12 sont à vérifier (`ls /sys/class/pwm`), puis à reporter dans `robot.yaml`.

### 2. Moteurs, roues en l'air

Posez le robot sur une cale, roues dans le vide.

```bash
ros2 launch musia_bringup robot.launch.py lidar:=false head:=false imu:=false battery:=false camera:=false
ros2 topic echo /musia/base/status          # connected: true, firmware renseigné
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

- Touche `i` : les quatre roues doivent tourner vers l'avant.
- Touche `j` : les roues gauches reculent et les roues droites avancent.

Si un côté tourne à l'envers, changez `INVERSER_GAUCHE` ou `INVERSER_DROITE` dans le firmware et téléversez de nouveau. Arrêtez le clavier : les roues doivent s'arrêter d'elles-mêmes en moins d'une demi-seconde (chien de garde).

### 3. Étalonnage au sol (moteurs sans encodeur)

1. **`deadband_pwm`** : le plus petit PWM qui fait démarrer le robot posé au sol.
2. **`max_wheel_speed_mps`** : distance parcourue en 2 s à PWM 255, divisée par 2.
3. **`slip_factor`** : commander `angular.z = 1` pendant 6,3 s (un tour). Si le robot ne fait qu'un demi-tour, doubler le facteur.

Reportez les valeurs dans `config/robot.yaml`. Relancez `colcon build` si vous n'avez pas utilisé `--symlink-install`.

### 4. Capteurs

```bash
ros2 launch musia_bringup robot.launch.py
ros2 topic hz /scan                 # une dizaine de Hz
ros2 topic echo /imu/data_raw --once
ros2 topic echo /musia/battery --once
ros2 topic hz /camera/image_raw
```

Dans RViz (sur un PC du même réseau, `ROS_DOMAIN_ID` identique), affichez `/scan` dans le repère `base_link`. Un mur devant le robot doit apparaître devant, pas derrière ni sur le côté. Sinon, corrigez l'orientation de `base_laser` dans `urdf/musia.urdf` ou le paramètre `laser_scan_dir` du lidar.

### 5. Prototype complet

```bash
ros2 launch musia_bringup prototype.launch.py
```

Sur un téléphone ou un PC du même Wi-Fi, ouvrez `http://<adresse-du-robot>:8080/` (adresse : `hostname -I` sur le Pi).

- **Piloter** : joystick (ou flèches du clavier) et curseur de vitesse. Relâcher arrête le robot. Toucher le joystick pendant une navigation annule la destination : vous reprenez la main.
- **Explorer** : roulez doucement dans la pièce. La carte se dessine en direct (gris : inconnu, blanc : libre, noir : obstacle).
- **Aller à un endroit** : touchez la carte. Le robot calcule son chemin, contourne les obstacles et s'arrête à destination, tourné dans la direction du trajet.
- **Points A et B** : conduisez le robot au point A, tapez « A » puis « Enregistrer ici ». Faites de même pour B. Les boutons « Aller » l'envoient ensuite de l'un à l'autre.
- **STOP** : bloque les moteurs et annule la destination, quelle que soit la source de commande. Un second appui relâche l'arrêt.
- **Tête** : curseur de gauche à droite, bouton « centre ».

L'arrêt d'urgence de la page passe par le Wi-Fi : **gardez la main près de l'interrupteur de la batterie** pendant les premiers essais.

### Garder les points d'une séance à l'autre

En exploration, la carte et ses coordonnées naissent là où le robot démarre. Deux façons de retrouver les points A et B :

- **Simple** : marquez au sol la position et la direction de départ du robot, et repartez toujours de là.
- **Durable** : enregistrez la carte, puis relancez en mode carte :

```bash
ros2 run nav2_map_server map_saver_cli -f ~/cartes/salle
ros2 launch musia_bringup prototype.launch.py slam:=False map:=$HOME/cartes/salle.yaml
```

En mode carte, il faut indiquer au robot où il se trouve au démarrage : outil « 2D Pose Estimate » dans RViz, ou départ toujours au même endroit.

Les points sont enregistrés sur le Pi, dans `~/.config/musia/points.json`.

## Limites connues du prototype

- **Obstacles bas** : le lidar balaie à environ 25 cm du sol. Il ne voit ni les marches, ni les pieds de chaise, ni les objets posés par terre.
- **Odométrie** : sans encodeur, la position vient du lidar et du gyroscope. Dans un couloir long et lisse, ou une pièce vide, le robot peut mal estimer sa progression.
- **Sécurité réseau** : la page n'a pas de mot de passe. N'utilisez le robot que sur un Wi-Fi de confiance (point d'accès du Pi ou réseau privé).
- **Vitesse** : 0,25 m/s au maximum, réglable dans `robot.yaml` (`max_linear_mps`) et `nav2_params.yaml` (`vx_max`).

## Points à valider sur le robot

- le pilote lidar avec le STL-19P (réglé comme un LD19) ;
- rf2o sous Jazzy ;
- l'orientation du lidar et de l'IMU dans l'URDF ;
- le type de message de twist_mux et de Nav2 sous Jazzy (`Twist` attendu partout ; si l'un publie `TwistStamped`, régler `enable_stamped_cmd_vel` ou `use_stamped`) ;
- la charge du Pi 5 avec slam_toolbox, Nav2 et la vidéo en même temps (baisser `image_size` ou désactiver la caméra si besoin).

## Câblage prévu sur le Pi 5

| Fonction | Broche GPIO (physique) | Remarque |
|---|---|---|
| I²C SDA / SCL (IMU 0x68, INA219 0x40) | GPIO 2 / 3 (3 / 5) | Par le connecteur de module |
| Servo de la tête, signal | GPIO 12 (32) | PWM matériel ; broche libre du connecteur de module |
| I²S : BCLK, LRCLK, DIN micro, DOUT ampli | GPIO 18, 19, 20, 21 (12, 35, 38, 40) | Réservées pour la voix (en pause) : pas de PWM sur 18 et 19 |
| Arduino, lidar, webcam | USB | Ports nommés par `/dev/serial/by-id/` |

Alimentez le servo par un 5 V séparé de celui du Pi, avec une masse commune. Ses pointes de courant peuvent sinon faire redémarrer le Pi.

## Tests sans ROS (sur n'importe quel PC)

```bash
cd ros2_ws/src && python -m pytest
```

Les tests couvrent :
- le protocole série et la cinématique ;
- les pilotes I²C et PWM ;
- la page de pilotage : points nommés, image de la carte, état de navigation, serveur HTTP avec un faux robot ;
- la visite et la voix, en pause.

## Ce qui change par rapport à l'ancien code

L'ancien code utile se trouvait sur des branches sans historique commun : `Main` (ROS 2) et `feature/design-system` (serveur NLP, admin, sketch Arduino). `main` ne contenait que des squelettes. Défauts relevés et corrigés :

- **Le robot ne pouvait pas rouler.** Le pont ROS parlait un protocole (`M:dir,pwm,…` et trames JSON) qu'aucun firmware n'implémentait : le seul sketch était celui du kit Keyestudio, piloté en Bluetooth. Un nouveau firmware et un protocole commun, avec somme de contrôle, sont testés côté Pi.
- **Les paramètres étaient ignorés sans message.** `musia_params.yaml` utilisait `port` et `ultrasonic_threshold_cm`, alors que le nœud lisait `serial_port` et `person_threshold_cm`.
- **Les services plantaient à l'appel.** `SetTourMode` lisait `request.mode` alors que le service définit `target_state`. `GetCurrentZone` remplissait des champs inexistants.
- **La visite restait bloquée devant la première œuvre** : l'état PRESENTATION ne sortait jamais. Corrigé dans `musia_tour`, en pause.
- **Il n'y avait pas d'odométrie exploitable.** L'EKF ne fusionnait que l'accéléromètre, qui dérive en quelques secondes, et aucun lancement ne démarrait le lidar. L'odométrie vient maintenant du lidar (rf2o), corrigée par le gyroscope.
- **Il n'y avait ni pilotage à distance ni arbitrage des commandes.** twist_mux donne maintenant la priorité à l'arrêt d'urgence, puis au pilote, puis à Nav2.
- **Des produits de compilation étaient versionnés** (`build/`, `install/`, `log/`, `__pycache__`), ainsi que des enregistrements de visiteurs (`.wav`). `.gitignore` bloque désormais ces fichiers.
- **Le matériel a changé.** Il n'y a plus de capteur à ultrasons ni de matrice LED. Il y a maintenant :
  - une webcam USB sur un servo ;
  - un lidar au sommet ;
  - l'IMU et l'INA219 sur l'I²C du Pi ;
  - le micro et l'ampli en I²S.

**La carte `maps/famienkro.pgm` reprise de l'ancien code est vide** (aucun obstacle, 10 × 10 m). Elle ne sert que de valeur par défaut au mode carte. Le prototype n'en a pas besoin : il cartographie en roulant.

## Après le prototype (en pause)

1. **Détection de visiteur** à la webcam, publiée sur `/musia/person` ; la tête la suit (`follow_person`).
2. **Voix** (`musia_voice`) : synthèse et écoute sur le micro et l'ampli I²S.
3. **Visite guidée** (`musia_tour`, `musia.launch.py`).
4. **Serveur Python unique** (FastAPI) et interface d'administration.
