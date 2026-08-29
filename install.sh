#!/usr/bin/env bash
# =============================================================================
#  AdaDo Installer
#  Usage: curl -fsSL https://adado.diginoz.com.au/install.sh | bash
# =============================================================================
set -euo pipefail

# ── Colours ──────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'
  YELLOW='\033[0;33m'
  GREEN='\033[0;32m'
  CYAN='\033[0;36m'
  BOLD='\033[1m'
  RESET='\033[0m'
else
  RED='' YELLOW='' GREEN='' CYAN='' BOLD='' RESET=''
fi

info()    { printf "${CYAN}  →${RESET} %s\n" "$*"; }
success() { printf "${GREEN}  ✓${RESET} %s\n" "$*"; }
warn()    { printf "${YELLOW}  ⚠${RESET} %s\n" "$*"; }
error()   { printf "${RED}  ✗${RESET} %s\n" "$*" >&2; }
fatal()   { error "$*"; exit 1; }
header()  { printf "\n${BOLD}${CYAN}%s${RESET}\n" "$*"; }
divider() { printf "${CYAN}%s${RESET}\n" "──────────────────────────────────────────────"; }

# ── Banner ────────────────────────────────────────────────────────────────────
printf "\n"
printf "${BOLD}${CYAN}"
cat << 'EOF'
    _       _       ___
   / \   __| | __ _|   \  ___
  / _ \ / _` |/ _` | |) |/ _ \
 / ___ \ (_| | (_| |   /|  __/
/_/   \_\__,_|\__,_|_|\_\ \___|

EOF
printf "${RESET}"
printf "  ${BOLD}Your home. Your AI. Your rules.${RESET}\n"
printf "  Self-hosted AI suite by Diginoz\n\n"
divider

# ── OS Detection ──────────────────────────────────────────────────────────────
header "Checking your system"

OS=""
DISTRO=""

if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="macos"
  DISTRO="macOS $(sw_vers -productVersion 2>/dev/null || echo '')"
elif [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  case "${ID:-}" in
    ubuntu)  OS="debian"; DISTRO="Ubuntu ${VERSION_ID:-}" ;;
    debian)  OS="debian"; DISTRO="Debian ${VERSION_ID:-}" ;;
    *)
      if echo "${ID_LIKE:-}" | grep -qi "debian"; then
        OS="debian"; DISTRO="${PRETTY_NAME:-Linux}"
      else
        warn "Unrecognised distro: ${PRETTY_NAME:-unknown}. Continuing anyway…"
        OS="linux"; DISTRO="${PRETTY_NAME:-Linux}"
      fi
      ;;
  esac
else
  warn "Cannot detect OS — assuming generic Linux."
  OS="linux"; DISTRO="Linux"
fi

success "Detected: ${DISTRO}"

# ── Helper: command exists ────────────────────────────────────────────────────
has() { command -v "$1" &>/dev/null; }

# ── Docker check ──────────────────────────────────────────────────────────────
header "Checking Docker"

if ! has docker; then
  error "Docker is not installed."
  printf "\n  Install it from one of these sources:\n\n"
  if [[ "$OS" == "macos" ]]; then
    printf "    ${BOLD}macOS${RESET}  →  https://docs.docker.com/desktop/mac/install/\n"
    printf "             or:  brew install --cask docker\n"
  else
    printf "    ${BOLD}Ubuntu/Debian${RESET}  →  https://docs.docker.com/engine/install/ubuntu/\n"
    printf "    Quick install:  curl -fsSL https://get.docker.com | bash\n"
  fi
  printf "\n  After installing Docker, re-run this installer.\n\n"
  exit 1
fi

DOCKER_VERSION=$(docker --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
success "Docker ${DOCKER_VERSION} found"

# Verify Docker daemon is running
if ! docker info &>/dev/null; then
  error "Docker daemon is not running."
  if [[ "$OS" == "macos" ]]; then
    printf "\n  Start Docker Desktop from your Applications folder.\n\n"
  else
    printf "\n  Try:  sudo systemctl start docker\n"
    printf "   or:  sudo service docker start\n\n"
  fi
  exit 1
fi
success "Docker daemon is running"

# ── Docker Compose v2 check ───────────────────────────────────────────────────
header "Checking Docker Compose"

COMPOSE_CMD=""

if docker compose version &>/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
  COMPOSE_VERSION=$(docker compose version --short 2>/dev/null || docker compose version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
  success "Docker Compose v2 (plugin) ${COMPOSE_VERSION} found"
elif has docker-compose; then
  DC_VER=$(docker-compose --version 2>&1 | grep -oE '[0-9]+' | head -1)
  if [[ "${DC_VER:-0}" -ge 2 ]]; then
    COMPOSE_CMD="docker-compose"
    success "Docker Compose v2 (standalone) found"
  else
    error "Docker Compose v1 is installed but AdaDo requires v2."
    printf "\n  Upgrade guide:  https://docs.docker.com/compose/migrate/\n"
    printf "  Quick install:  https://docs.docker.com/compose/install/\n\n"
    exit 1
  fi
else
  error "Docker Compose v2 is not installed."
  printf "\n  Install it:\n"
  printf "    Ubuntu/Debian:  sudo apt-get install docker-compose-plugin\n"
  printf "    macOS:          Included in Docker Desktop\n"
  printf "    Manual:         https://docs.docker.com/compose/install/\n\n"
  exit 1
fi

# ── Git check ─────────────────────────────────────────────────────────────────
header "Checking Git"

if ! has git; then
  error "Git is not installed."
  if [[ "$OS" == "macos" ]]; then
    printf "\n  Install with:  brew install git\n"
    printf "   or install Xcode Command Line Tools:  xcode-select --install\n\n"
  else
    printf "\n  Install with:  sudo apt-get install -y git\n\n"
  fi
  exit 1
fi

GIT_VERSION=$(git --version | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
success "Git ${GIT_VERSION} found"

# ── Clone repository ──────────────────────────────────────────────────────────
header "Installing AdaDo"

INSTALL_DIR="${HOME}/adado"
REPO_URL="https://github.com/diginoz-com-au/adado-apps"

if [[ -d "${INSTALL_DIR}/.git" ]]; then
  info "AdaDo directory already exists at ${INSTALL_DIR}"
  info "Pulling latest changes…"
  git -C "${INSTALL_DIR}" pull --ff-only || {
    warn "Could not fast-forward. Your local changes may conflict."
    warn "To reset:  git -C ${INSTALL_DIR} reset --hard origin/main"
  }
  success "Repository updated"
else
  if [[ -d "${INSTALL_DIR}" ]] && [[ -n "$(ls -A "${INSTALL_DIR}" 2>/dev/null)" ]]; then
    fatal "Directory ${INSTALL_DIR} exists and is not empty. Remove it first or install elsewhere."
  fi
  info "Cloning ${REPO_URL} → ${INSTALL_DIR}"
  git clone "${REPO_URL}" "${INSTALL_DIR}" || fatal "Failed to clone repository. Check your internet connection."
  success "Repository cloned"
fi

cd "${INSTALL_DIR}"

# ── Environment configuration ─────────────────────────────────────────────────
header "Configuring environment"

ENV_FILE="${INSTALL_DIR}/harness/.env"
ENV_EXAMPLE="${INSTALL_DIR}/harness/.env.example"

if [[ ! -f "${ENV_EXAMPLE}" ]]; then
  warn ".env.example not found — skipping environment setup."
  warn "You may need to create ${ENV_FILE} manually."
else
  if [[ -f "${ENV_FILE}" ]]; then
    warn "${ENV_FILE} already exists — skipping prompts (delete it to reconfigure)."
  else
    info "Copying .env.example → .env"
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"

    printf "\n"
    divider
    printf "  ${BOLD}Quick setup${RESET} — press Enter to keep defaults\n"
    divider

    # Prompt: domain name
    printf "\n"
    DEFAULT_DOMAIN="localhost"
    read -rp "  Domain name (e.g. adado.example.com) [${DEFAULT_DOMAIN}]: " USER_DOMAIN
    DOMAIN="${USER_DOMAIN:-$DEFAULT_DOMAIN}"

    # Prompt: admin password
    printf "\n"
    DEFAULT_PASS="$(openssl rand -base64 16 2>/dev/null | tr -dc 'a-zA-Z0-9' | head -c 20 || echo 'changeme')"
    read -rsp "  Admin password [auto-generated]: " USER_PASS
    printf "\n"
    ADMIN_PASS="${USER_PASS:-$DEFAULT_PASS}"

    # Write values into .env
    sed_inplace() {
      if [[ "$OS" == "macos" ]]; then
        sed -i '' "$@"
      else
        sed -i "$@"
      fi
    }

    # Derive base URL (https for real domains, http for localhost)
    if [[ "${DOMAIN}" == "localhost" || "${DOMAIN}" == "127.0.0.1" ]]; then
      BASE_URL="http://${DOMAIN}"
    else
      BASE_URL="https://${DOMAIN}"
    fi

    if grep -q "^ADADO_DOMAIN=" "${ENV_FILE}"; then
      sed_inplace "s|^ADADO_DOMAIN=.*|ADADO_DOMAIN=${DOMAIN}|" "${ENV_FILE}"
    else
      printf "\nADADO_DOMAIN=%s\n" "${DOMAIN}" >> "${ENV_FILE}"
    fi

    if grep -q "^ADADO_BASE_URL=" "${ENV_FILE}"; then
      sed_inplace "s|^ADADO_BASE_URL=.*|ADADO_BASE_URL=${BASE_URL}|" "${ENV_FILE}"
    else
      printf "ADADO_BASE_URL=%s\n" "${BASE_URL}" >> "${ENV_FILE}"
    fi

    if grep -q "^ADADO_DB_PASSWORD=" "${ENV_FILE}"; then
      sed_inplace "s|^ADADO_DB_PASSWORD=.*|ADADO_DB_PASSWORD=${ADMIN_PASS}|" "${ENV_FILE}"
    else
      printf "ADADO_DB_PASSWORD=%s\n" "${ADMIN_PASS}" >> "${ENV_FILE}"
    fi

    success "Environment configured"
    if [[ -z "${USER_PASS:-}" ]]; then
      printf "\n  ${YELLOW}${BOLD}Auto-generated admin password:${RESET} ${BOLD}${ADMIN_PASS}${RESET}\n"
      printf "  ${YELLOW}Save this — it won't be shown again.${RESET}\n\n"
    fi
  fi
fi

# ── Launch core stack ─────────────────────────────────────────────────────────
header "Starting AdaDo (core profile)"

info "Running: ${COMPOSE_CMD} --profile core up -d"
printf "\n"

cd "${INSTALL_DIR}/harness"
${COMPOSE_CMD} --profile core up -d || {
  printf "\n"
  error "Docker Compose failed. Common causes:"
  printf "    • Port already in use — check:  sudo lsof -i :80 -i :443\n"
  printf "    • Permission denied  — try:     sudo usermod -aG docker \$USER  then re-login\n"
  printf "    • Missing .env vars  — edit:    ${ENV_FILE}\n"
  exit 1
}

# ── Read configured domain for final message ──────────────────────────────────
PORTAL_DOMAIN="localhost"
if [[ -f "${ENV_FILE}" ]]; then
  PORTAL_DOMAIN=$(grep -E "^ADADO_DOMAIN=" "${ENV_FILE}" | cut -d= -f2- | tr -d '"' | tr -d "'" || echo "localhost")
fi

# ── Success ───────────────────────────────────────────────────────────────────
printf "\n"
divider
printf "${GREEN}${BOLD}\n"
printf "  AdaDo is running!\n"
printf "${RESET}"
divider
printf "\n"
printf "  ${BOLD}Portal:${RESET}      http://${PORTAL_DOMAIN}\n"
printf "  ${BOLD}Install dir:${RESET} ${INSTALL_DIR}\n"
printf "  ${BOLD}Config:${RESET}      ${ENV_FILE}\n"
printf "\n"
printf "  ${BOLD}Add more apps:${RESET}\n"
printf "    cd ${INSTALL_DIR}/harness\n"
printf "    ${COMPOSE_CMD} --profile <appname> up -d\n"
printf "\n"
printf "  ${BOLD}View running services:${RESET}\n"
printf "    ${COMPOSE_CMD} ps\n"
printf "\n"
printf "  ${BOLD}Stop everything:${RESET}\n"
printf "    ${COMPOSE_CMD} down\n"
printf "\n"
printf "  ${BOLD}Docs:${RESET}        https://github.com/diginoz-com-au/adado-apps\n"
printf "\n"
divider
printf "\n"
