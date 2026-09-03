#!/usr/bin/env bash
# FreeLingo setup script — run on a fresh machine to get the app running.
set -euo pipefail

REPO_URL="https://github.com/sonnguyenthanh193/freelingo.git"
REPO_DIR="freelingo"
BACKEND_IMAGE="freelingo-backend:local"

echo "=== FreeLingo Setup ==="

# ── 1. Check Docker ──────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo "Docker not found. Installing..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "Docker installed. You may need to log out and back in for group changes."
fi

if ! docker compose version &>/dev/null; then
  echo "ERROR: docker compose plugin not found. Please install Docker Compose v2."
  exit 1
fi

# ── 2. Clone or pull repo ────────────────────────────────────────
if [ -d "$REPO_DIR/.git" ]; then
  echo "Repo already exists. Pulling latest..."
  cd "$REPO_DIR" && git pull origin main
else
  echo "Cloning repo..."
  git clone "$REPO_URL" "$REPO_DIR"
  cd "$REPO_DIR"
fi

# ── 3. Create .env if missing ────────────────────────────────────
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    # Generate random secrets
    DB_PASS=$(openssl rand -hex 16)
    REDIS_PASS=$(openssl rand -hex 16)
    SECRET=$(openssl rand -hex 32)
    sed -i "s/CHANGE_ME_DB_PASSWORD/$DB_PASS/" .env
    sed -i "s/CHANGE_ME_REDIS_PASSWORD/$REDIS_PASS/" .env
    sed -i "s/CHANGE_ME_SECRET_KEY/$SECRET/" .env
    echo ".env created with random secrets. Review and edit if needed."
  else
    echo "ERROR: No .env or .env.example found."
    exit 1
  fi
else
  echo ".env already exists. Skipping."
fi

# ── 4. Build backend image ──────────────────────────────────────
echo "Building backend image..."
docker build -t "$BACKEND_IMAGE" ./backend

# ── 5. Start services ───────────────────────────────────────────
echo "Starting services..."
docker compose up -d

echo ""
echo "=== Setup complete! ==="
echo "App is running at http://localhost:3000"
echo ""
echo "Useful commands:"
echo "  docker compose logs -f          # follow logs"
echo "  docker compose ps               # check status"
echo "  docker compose down             # stop services"
echo "  docker compose up -d --build    # rebuild and restart"
