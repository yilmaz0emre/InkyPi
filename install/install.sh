#!/bin/bash

# =============================================================================
# Script Name: install.sh
# Description: This script automates the installation of InkyPI and creation of
#              the InkyPI service.
#
# Usage: ./install.sh [-W <waveshare_device>]
#        -W <waveshare_device> (optional) Install for a Waveshare device,
#                               specifying the device model type, e.g. epd7in3e.
#
#                               Alternatively the path to the driver within the
#                               waveshareteam/e-Paper repository can be given,
#                               e.g. E-paper_Separate_Program/3.6inch_e-Paper_E/
#                               RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd3in6e.py
#
#                               If not specified then the Pimoroni Inky display
#                               is assumed.
#
#        -S                     (optional) Server mode: no panel attached. InkyPi runs
#                               the web UI and renders images for thin clients
#                               (e.g. on a Raspberry Pi 5). Use -R to set the
#                               resolution of the clients' panel.
#        -R <width>x<height>    (optional, with -S) Panel resolution, default 800x480.
#        -C <server_url>        (optional) Thin client mode: show the images rendered
#                               by an InkyPi server, e.g. -C http://pi5.local
#                               Combine with -W for a Waveshare panel. No web UI and
#                               no Chromium are installed on the client.
# =============================================================================

# Formatting stuff
bold=$(tput bold)
normal=$(tput sgr0)
red=$(tput setaf 1)
green=$(tput setaf 2)

SOURCE=${BASH_SOURCE[0]}
while [ -h "$SOURCE" ]; do # resolve $SOURCE until the file is no longer a symlink
  DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )
  SOURCE=$(readlink "$SOURCE")
  [[ $SOURCE != /* ]] && SOURCE=$DIR/$SOURCE
done
SCRIPT_DIR=$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )

APPNAME="inkypi"
INSTALL_PATH="/usr/local/$APPNAME"
SRC_PATH="$SCRIPT_DIR/../src"
BINPATH="/usr/local/bin"
VENV_PATH="$INSTALL_PATH/venv_$APPNAME"

SERVICE_FILE="$APPNAME.service"
SERVICE_FILE_SOURCE="$SCRIPT_DIR/$SERVICE_FILE"
SERVICE_FILE_TARGET="/etc/systemd/system/$SERVICE_FILE"

APT_REQUIREMENTS_FILE="$SCRIPT_DIR/debian-requirements.txt"
PIP_REQUIREMENTS_FILE="$SCRIPT_DIR/requirements.txt"

#
# Additional requirements for Waveshare support.
#
# empty means no WS support required, otherwise we expect the type of display
# as per the WS naming convention.
WS_TYPE=""
WS_REQUIREMENTS_FILE="$SCRIPT_DIR/ws-requirements.txt"

# Server / thin client modes (see usage above)
SERVER_MODE=""
SERVER_RESOLUTION="800x480"
CLIENT_SERVER_URL=""
SERVICE_DROPIN_DIR="/etc/systemd/system/$APPNAME.service.d"
SERVICE_DROPIN_FILE="$SERVICE_DROPIN_DIR/mode.conf"

# Where the driver is fetched from in the Waveshare e-Paper repository.  Most
# models live in the common driver directory, but models that are only
# published under E-paper_Separate_Program can override WS_DRIVER_DIR by
# passing a repo relative path to -W (see resolve_waveshare_type).
WS_DRIVER_REPO="waveshareteam/e-Paper"
WS_DRIVER_BRANCH="master"
WS_DRIVER_DEFAULT_DIR="RaspberryPi_JetsonNano/python/lib/waveshare_epd"
WS_DRIVER_DIR="$WS_DRIVER_DEFAULT_DIR"

# Work out the screen type, and where its driver lives, from the -W argument.
#
# The argument is either a bare model name, e.g. epd7in3e, or the path to the
# driver within the waveshareteam/e-Paper repository, e.g.
# E-paper_Separate_Program/3.6inch_e-Paper_E/RaspberryPi_JetsonNano/python/lib/waveshare_epd/epd3in6e.py
#
# The path form is needed for the models that are not published in the common
# driver directory.  Either way WS_TYPE ends up as the bare module name, e.g.
# epd3in6e, as that is what is written to device.json and imported at runtime.
resolve_waveshare_type() {
    local arg=${1#/}

    if [[ $arg == */* ]]; then
        WS_DRIVER_DIR=${arg%/*}
        arg=${arg##*/}
    fi

    WS_TYPE=${arg%.py}

    if [[ -z $WS_TYPE ]]; then
        echo "Option -W requires the model type of the Waveshare screen." >&2
        exit 1
    fi
}

# Parse the arguments, looking for the -W option.
parse_arguments() {
    while getopts ":W:SR:C:" opt; do
        case $opt in
            W) resolve_waveshare_type "$OPTARG"
                echo "Optional parameter WS is set for Waveshare support.  Screen type is: $WS_TYPE"
                ;;
            S) SERVER_MODE="true"
                echo "Server mode: no panel, rendering for thin clients."
                ;;
            R) SERVER_RESOLUTION="$OPTARG"
                ;;
            C) CLIENT_SERVER_URL="${OPTARG%/}"
                echo "Thin client mode: showing images from $CLIENT_SERVER_URL"
                ;;
            \?) echo "Invalid option: -$OPTARG." >&2
                exit 1
                ;;
            :) echo "Option -$OPTARG requires a value." >&2
               exit 1
               ;;
        esac
    done

    if [[ -n "$SERVER_MODE" && -n "$CLIENT_SERVER_URL" ]]; then
        echo "Options -S (server) and -C (thin client) cannot be combined." >&2
        exit 1
    fi
    if [[ -n "$SERVER_MODE" && -n "$WS_TYPE" ]]; then
        echo "Option -W is for the device with the panel (use it with -C), not with -S." >&2
        exit 1
    fi
    if [[ ! "$SERVER_RESOLUTION" =~ ^[0-9]+x[0-9]+$ ]]; then
        echo "Invalid resolution '$SERVER_RESOLUTION', expected e.g. 800x480." >&2
        exit 1
    fi
    if [[ -n "$CLIENT_SERVER_URL" && ! "$CLIENT_SERVER_URL" =~ ^https?:// ]]; then
        echo "Invalid server URL '$CLIENT_SERVER_URL', expected e.g. http://pi5.local" >&2
        exit 1
    fi
}

check_permissions() {
  # Ensure the script is run with sudo
  if [ "$EUID" -ne 0 ]; then
    echo_error "ERROR: Installation requires root privileges. Please run it with sudo."
    exit 1
  fi
}

# Stop before changing anything if an earlier apt/dpkg run was interrupted: apt refuses to install
# in that state, which would otherwise leave a half-finished installation.
check_package_system() {
  if [[ -n "$(dpkg --audit 2>/dev/null)" ]]; then
    echo_error "ERROR: The package system has unfinished installs (an earlier upgrade was interrupted).
Fix it first with:  sudo dpkg --configure -a && sudo apt-get -f install
then run this installer again."
    exit 1
  fi
}

# show_loader cannot report apt's exit status, so check the result explicitly.
verify_debian_dependencies() {
  local missing=()
  local package
  while read -r package; do
    [[ -z "$package" || "$package" == \#* ]] && continue
    [[ -n "$CLIENT_SERVER_URL" && "$package" == chromium* ]] && continue
    dpkg -s "$package" > /dev/null 2>&1 || missing+=("$package")
  done < "$APT_REQUIREMENTS_FILE"
  if (( ${#missing[@]} )); then
    echo_error "ERROR: These system packages could not be installed: ${missing[*]}
Check 'sudo apt-get install ${missing[*]}' for the reason, then run this installer again."
    exit 1
  fi
  echo_success "\tAll system dependencies are installed."
}

fetch_waveshare_driver() {
  echo "Fetching Waveshare driver for: $WS_TYPE"

  DRIVER_DEST="$SRC_PATH/display/waveshare_epd"
  DRIVER_FILE="$DRIVER_DEST/$WS_TYPE.py"
  DRIVER_BASE_URL="https://raw.githubusercontent.com/$WS_DRIVER_REPO/$WS_DRIVER_BRANCH/$WS_DRIVER_DIR"
  DRIVER_URL="$DRIVER_BASE_URL/$WS_TYPE.py"

  # Attempt to download the file
  if [ -f "$DRIVER_FILE" ]; then
    echo_success "\tWaveshare driver '$WS_TYPE.py' already exists at $DRIVER_FILE"
  elif curl --silent --fail -o "$DRIVER_FILE" "$DRIVER_URL"; then
    echo_success "\tWaveshare driver '$WS_TYPE.py' successfully downloaded to $DRIVER_FILE"
  else
    rm -f "$DRIVER_FILE"
    echo_error "ERROR: Failed to download Waveshare driver '$WS_TYPE.py' from:"
    echo_error "$DRIVER_URL"
    echo_error "Ensure the model name is correct and exists at:"
    echo_error "https://github.com/$WS_DRIVER_REPO/tree/$WS_DRIVER_BRANCH/$WS_DRIVER_DIR"
    exit 1
  fi

  # Prefer the epdconfig.py that sits alongside the driver, as the separate
  # programs ship their own copy, falling back to the common one.
  EPD_CONFIG_FILE="$DRIVER_DEST/epdconfig.py"
  EPD_CONFIG_URL="$DRIVER_BASE_URL/epdconfig.py"
  EPD_CONFIG_FALLBACK_URL="https://raw.githubusercontent.com/$WS_DRIVER_REPO/$WS_DRIVER_BRANCH/$WS_DRIVER_DEFAULT_DIR/epdconfig.py"
  if [ -f "$EPD_CONFIG_FILE" ]; then
    echo_success "\tWaveshare epdconfig file already exists at $EPD_CONFIG_FILE"
  elif curl --silent --fail -o "$EPD_CONFIG_FILE" "$EPD_CONFIG_URL"; then
    echo_success "\tWaveshare epdconfig file successfully downloaded to $EPD_CONFIG_FILE"
  elif curl --silent --fail -o "$EPD_CONFIG_FILE" "$EPD_CONFIG_FALLBACK_URL"; then
    echo_success "\tWaveshare epdconfig file successfully downloaded to $EPD_CONFIG_FILE"
  else
    rm -f "$EPD_CONFIG_FILE"
    echo_error "ERROR: Failed to download Waveshare epdconfig file."
    exit 1
  fi
}

enable_interfaces(){
  echo "Enabling interfaces required for $APPNAME"
  #enable spi
  sudo sed -i 's/^dtparam=spi=.*/dtparam=spi=on/' /boot/firmware/config.txt
  sudo sed -i 's/^#dtparam=spi=.*/dtparam=spi=on/' /boot/firmware/config.txt
  sudo raspi-config nonint do_spi 0
  echo_success "\tSPI Interface has been enabled."
  #enable i2c
  sudo sed -i 's/^dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' /boot/firmware/config.txt
  sudo sed -i 's/^#dtparam=i2c_arm=.*/dtparam=i2c_arm=on/' /boot/firmware/config.txt
  sudo raspi-config nonint do_i2c 0
  echo_success "\tI2C Interface has been enabled."

  # Is a Waveshare device specified as an install parameter?
  if [[ -n "$WS_TYPE" ]]; then
    # WS parameter is set for Waveshare support so ensure that both CS lines
    # are enabled in the config.txt file.  This is different to INKY which
    # only needs one line set.n
    echo "Enabling both CS lines for SPI interface in config.txt"
    if ! grep -E -q '^[[:space:]]*dtoverlay=spi0-2cs' /boot/firmware/config.txt; then
        sed -i '/^dtparam=spi=on/a dtoverlay=spi0-2cs' /boot/firmware/config.txt
    else
        echo "dtoverlay for spi0-2cs already specified"
    fi
  else
    # TODO - check if really need the dtparam set for INKY as this seems to be 
    # only for the older screens (as per INKY docs)
    echo "Enabling single CS line for SPI interface in config.txt"
    if ! grep -E -q '^[[:space:]]*dtoverlay=spi0-0cs' /boot/firmware/config.txt; then
        sed -i '/^dtparam=spi=on/a dtoverlay=spi0-0cs' /boot/firmware/config.txt
    else
        echo "dtoverlay for spi0-0cs already specified"
    fi
  fi 
}

show_loader() {
  local pid=$!
  local delay=0.1
  local spinstr='|/-\'
  printf "$1 [${spinstr:0:1}] "
  while ps a | awk '{print $1}' | grep -q "${pid}"; do
    local temp=${spinstr#?}
    printf "\r$1 [${temp:0:1}] "
    spinstr=${temp}${spinstr%"${temp}"}
    sleep ${delay}
  done
  if [[ $? -eq 0 ]]; then
    printf "\r$1 [\e[32m\xE2\x9C\x94\e[0m]\n"
  else
    printf "\r$1 [\e[31m\xE2\x9C\x98\e[0m]\n"
  fi
}

echo_success() {
  echo -e "$1 [\e[32m\xE2\x9C\x94\e[0m]"
}

echo_override() {
  echo -e "\r$1"
}

echo_header() {
  echo -e "${bold}$1${normal}"
}

echo_error() {
  echo -e "${red}$1${normal} [\e[31m\xE2\x9C\x98\e[0m]\n"
}

echo_blue() {
  echo -e "\e[38;2;65;105;225m$1\e[0m"
}


install_debian_dependencies() {
  if [ -f "$APT_REQUIREMENTS_FILE" ]; then
    sudo apt-get update > /dev/null &
    show_loader "Fetch available system dependencies updates. " 

    # a thin client never renders HTML, so it does not need Chromium
    local requirements="$APT_REQUIREMENTS_FILE"
    if [[ -n "$CLIENT_SERVER_URL" ]]; then
      requirements=$(mktemp)
      grep -v '^chromium' "$APT_REQUIREMENTS_FILE" > "$requirements"
    fi
    xargs -a "$requirements" sudo apt-get install -y > /dev/null &
    show_loader "Installing system dependencies. "
  else
    echo "ERROR: System dependencies file $APT_REQUIREMENTS_FILE not found!"
    exit 1
  fi
}

setup_zramswap_service() {
  echo "Enabling and starting zramswap service."
  sudo apt-get install -y zram-tools > /dev/null
  echo -e "ALGO=zstd\nPERCENT=60" | sudo tee /etc/default/zramswap > /dev/null
  sudo systemctl enable --now zramswap
}

setup_earlyoom_service() {
  echo "Enabling and starting earlyoom service."
  sudo apt-get install -y earlyoom > /dev/null
  sudo systemctl enable --now earlyoom
}

create_venv(){
  echo "Creating python virtual environment. "
  python3 -m venv "$VENV_PATH"
  $VENV_PATH/bin/python -m pip install --upgrade pip setuptools wheel > /dev/null
  $VENV_PATH/bin/python -m pip install -r $PIP_REQUIREMENTS_FILE -qq > /dev/null &
  show_loader "\tInstalling python dependencies. "

  # do additional dependencies for Waveshare support.
  if [[ -n "$WS_TYPE" ]]; then
    echo "Adding additional dependencies for waveshare to the python virtual environment. "
    $VENV_PATH/bin/python -m pip install -r $WS_REQUIREMENTS_FILE > ws_pip_install.log &
    show_loader "\tInstalling additional Waveshare python dependencies. "
  fi

}

install_app_service() {
  echo "Installing $APPNAME systemd service."
  if [ -f "$SERVICE_FILE_SOURCE" ]; then
    cp "$SERVICE_FILE_SOURCE" "$SERVICE_FILE_TARGET"
    sudo systemctl daemon-reload
    sudo systemctl enable $SERVICE_FILE
  else
    echo_error "ERROR: Service file $SERVICE_FILE_SOURCE not found!"
    exit 1
  fi
}

install_executable() {
  echo "Adding executable to ${BINPATH}/$APPNAME"
  cp $SCRIPT_DIR/inkypi $BINPATH/
  sudo chmod +x $BINPATH/$APPNAME
}

install_config() {
  CONFIG_BASE_DIR="$SCRIPT_DIR/config_base"
  CONFIG_DIR="$SRC_PATH/config"
  echo "Copying config files to $CONFIG_DIR"

  # Check and copy device.config if it doesn't exist
  if [ ! -f "$CONFIG_DIR/device.json" ]; then
    cp "$CONFIG_BASE_DIR/device.json" "$CONFIG_DIR/"
    show_loader "\tCopying device.config to $CONFIG_DIR"
  else
    echo_success "\tdevice.json already exists in $CONFIG_DIR"
  fi
}

#
# Update the device.json file with the supplied Waveshare parameter (if set).
#
update_config() {
  if [[ -n "$WS_TYPE" ]]; then
      local DEVICE_JSON="$CONFIG_DIR/device.json"

      if grep -q '"display_type":' "$DEVICE_JSON"; then
          # Update existing display_type value
          sed -i "s/\"display_type\": \".*\"/\"display_type\": \"$WS_TYPE\"/" "$DEVICE_JSON"
          echo "Updated display_type to: $WS_TYPE" 
      else
          # Append display_type safely, ensuring proper comma placement
          if grep -q '}$' "$DEVICE_JSON"; then
              sed -i '$s/}/,/' "$DEVICE_JSON"  # Replace last } with a comma
          fi
          echo "  \"display_type\": \"$WS_TYPE\"" >> "$DEVICE_JSON"
          echo "}" >> "$DEVICE_JSON"  # Add trailing }
          echo "Added display_type: $WS_TYPE"
      fi
  else
      echo "Config not updated as WS_TYPE flag is not set"
  fi
}

#
# Server / thin client settings in device.json (uses jq from debian-requirements.txt).
#
configure_mode() {
  local DEVICE_JSON="$CONFIG_DIR/device.json"
  local tmp
  tmp=$(mktemp)
  if [[ -n "$SERVER_MODE" ]]; then
    local width=${SERVER_RESOLUTION%x*}
    local height=${SERVER_RESOLUTION#*x}
    if ! jq --argjson w "$width" --argjson h "$height" \
        '.display_type = "server" | .resolution = [$w, $h]' "$DEVICE_JSON" > "$tmp" || ! mv "$tmp" "$DEVICE_JSON"; then
      echo_error "ERROR: Could not write server settings to $DEVICE_JSON (is jq installed?)"
      exit 1
    fi
    echo_success "\tServer mode configured: display_type=server, resolution=${width}x${height}"
  elif [[ -n "$CLIENT_SERVER_URL" ]]; then
    if ! jq --arg url "$CLIENT_SERVER_URL" \
        '.thin_client = ((.thin_client // {}) + {server_url: $url})' "$DEVICE_JSON" > "$tmp" || ! mv "$tmp" "$DEVICE_JSON"; then
      echo_error "ERROR: Could not write thin client settings to $DEVICE_JSON (is jq installed?)"
      exit 1
    fi
    echo_success "\tThin client configured: server_url=$CLIENT_SERVER_URL"
  fi
  rm -f "$tmp"
}

#
# systemd drop-in for the selected mode; kept separate so updates of inkypi.service keep it.
#
install_service_mode() {
  if [[ -n "$SERVER_MODE" ]]; then
    mkdir -p "$SERVICE_DROPIN_DIR"
    # Chromium needs more than the default 200M; a Pi 5 has plenty to spare
    printf '# inkypi-mode: server\n[Service]\nCPUQuota=\nMemoryMax=1G\n' > "$SERVICE_DROPIN_FILE"
    echo_success "\tService configured for server mode"
  elif [[ -n "$CLIENT_SERVER_URL" ]]; then
    mkdir -p "$SERVICE_DROPIN_DIR"
    printf '# inkypi-mode: client\n[Service]\nExecStart=\nExecStart=%s client\nMemoryMax=150M\n' "$BINPATH/$APPNAME" > "$SERVICE_DROPIN_FILE"
    echo_success "\tService configured for thin client mode"
  elif [[ -f "$SERVICE_DROPIN_FILE" ]]; then
    rm -f "$SERVICE_DROPIN_FILE"
    echo_success "\tRemoved previous server/client service settings"
  fi
  sudo systemctl daemon-reload
}

stop_service() {
    echo "Checking if $SERVICE_FILE is running"
    if /usr/bin/systemctl is-active --quiet $SERVICE_FILE
    then
      /usr/bin/systemctl stop $SERVICE_FILE > /dev/null &
      show_loader "Stopping $APPNAME service"
    else  
      echo_success "\t$SERVICE_FILE not running"
    fi
}

start_service() {
  echo "Starting $APPNAME service."
  sudo systemctl start $SERVICE_FILE
}

install_src() {
  # Check if an existing installation is present
  echo "Installing $APPNAME to $INSTALL_PATH"
  if [[ -d $INSTALL_PATH ]]; then
    rm -rf "$INSTALL_PATH" > /dev/null
    show_loader "\tRemoving existing installation found at $INSTALL_PATH"
  fi

  mkdir -p "$INSTALL_PATH"

  ln -sf "$SRC_PATH" "$INSTALL_PATH/src"
  show_loader "\tCreating symlink from $SRC_PATH to $INSTALL_PATH/src"
}

install_cli() {
  cp -r "$SCRIPT_DIR/cli" "$INSTALL_PATH/"
  sudo chmod +x "$INSTALL_PATH/cli/"*
}

# Get Raspberry Pi hostname
get_hostname() {
  echo "$(hostname)"
}

# Get Raspberry Pi IP address
get_ip_address() {
  ip_address=$(hostname -I | awk '{print $1}')
  echo "$ip_address"
}

# Get OS release number, e.g. 11=Bullseye, 12=Bookworm, 13=Trixe
get_os_version() {
  echo "$(lsb_release -sr)"
}

ask_for_reboot() {
  # Get hostname and IP address
  hostname=$(get_hostname)
  ip_address=$(get_ip_address)
  echo_header "$(echo_success "${APPNAME^^} Installation Complete!")"
  echo_header "[•] A reboot of your Raspberry Pi is required for the changes to take effect"
  if [[ -n "$CLIENT_SERVER_URL" ]]; then
    echo_header "[•] Thin client: after the reboot the panel shows the images of $(echo_blue "$CLIENT_SERVER_URL"). Configure everything in that server's web UI."
  else
    echo_header "[•] After your Pi is rebooted, you can access the web UI by going to $(echo_blue "'$hostname.local'") or $(echo_blue "'$ip_address'") in your browser."
  fi
  echo_header "[•] If you encounter any issues or have suggestions, please submit them here: https://github.com/fatihak/InkyPi/issues"

  read -p "Would you like to restart your Raspberry Pi now? [Y/N] " userInput
  userInput="${userInput^^}"

  if [[ "${userInput,,}" == "y" ]]; then
    echo_success "You entered 'Y', rebooting now..."
    sleep 2
    sudo reboot now
  elif [[ "${userInput,,}" == "n" ]]; then
    echo "Please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    exit
  else
    echo "Unknown input, please restart your Raspberry Pi later to apply changes by running 'sudo reboot now'."
    sleep 1
  fi
}

# check if we have an argument for WS display support.  Parameter is not required
# to maintain default INKY display support.
parse_arguments "$@"
check_permissions
check_package_system
stop_service
# fetch the WS display driver if defined.
if [[ -n "$WS_TYPE" ]]; then
  fetch_waveshare_driver
fi
# a render server has no panel, so SPI/I2C stay untouched
if [[ -z "$SERVER_MODE" ]]; then
  enable_interfaces
fi
install_debian_dependencies
verify_debian_dependencies
# zramswap and earlyoom protect low-memory panel devices (Pi Zero). A render server usually has
# plenty of RAM and may run other services, which earlyoom could kill (e.g. a large LLM).
if [[ -n "$SERVER_MODE" ]]; then
  echo "Server mode - skipping zramswap and earlyoom setup."
else
  # check OS version for Bookworm to setup zramswap
  if [[ $(get_os_version) = "12" ]] ; then
    echo "OS version is Bookworm - setting up zramswap"
    setup_zramswap_service
  else
    echo "OS version is not Bookworm - skipping zramswap setup."
  fi
  setup_earlyoom_service
fi
install_src
install_cli
create_venv
install_executable
install_config
# update the config file with additional WS if defined.
if [[ -n "$WS_TYPE" ]]; then
  update_config
fi
configure_mode
install_app_service
install_service_mode

# the web UI assets are not needed on a thin client
if [[ -z "$CLIENT_SERVER_URL" ]]; then
  echo "Update JS and CSS files"
  bash $SCRIPT_DIR/update_vendors.sh > /dev/null
fi

ask_for_reboot
