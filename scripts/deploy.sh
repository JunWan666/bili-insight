#!/usr/bin/env bash
set -Eeuo pipefail

REPOSITORY="JunWan666/bili-insight"
case "$(uname -s 2>/dev/null || true)" in
  Linux) PLATFORM_DEFAULT_DEPLOY_DIR='/opt/bili-insight' ;;
  *) PLATFORM_DEFAULT_DEPLOY_DIR="${HOME:-.}/bili-insight" ;;
esac
DEFAULT_DEPLOY_DIR="${BILI_INSIGHT_DIR:-$PLATFORM_DEFAULT_DEPLOY_DIR}"
DEFAULT_HOST="127.0.0.1"
DEFAULT_PORT="8080"
DEFAULT_VERSION="latest"
DEFAULT_MODE="auto"

DEPLOY_DIR="$DEFAULT_DEPLOY_DIR"
WEB_HOST_VALUE="${BILI_INSIGHT_HOST:-$DEFAULT_HOST}"
WEB_PORT_VALUE="${BILI_INSIGHT_PORT:-$DEFAULT_PORT}"
RELEASE_VERSION="${BILI_INSIGHT_VERSION:-$DEFAULT_VERSION}"
DEPLOY_MODE="${BILI_INSIGHT_MODE:-$DEFAULT_MODE}"
ACTION=""
LOG_TARGET="all"
ACTIVE_STAGE=""
HOST_EXPLICIT=0
PORT_EXPLICIT=0

if [[ "${BILI_INSIGHT_HOST+x}" == 'x' ]]; then
  HOST_EXPLICIT=1
fi
if [[ "${BILI_INSIGHT_PORT+x}" == 'x' ]]; then
  PORT_EXPLICIT=1
fi

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_RESET='\033[0m'
  C_BOLD='\033[1m'
  C_DIM='\033[2m'
  C_RED='\033[31m'
  C_GREEN='\033[32m'
  C_YELLOW='\033[33m'
  C_BLUE='\033[34m'
  C_CYAN='\033[36m'
else
  C_RESET=''
  C_BOLD=''
  C_DIM=''
  C_RED=''
  C_GREEN=''
  C_YELLOW=''
  C_BLUE=''
  C_CYAN=''
fi

info() { printf '%b%s%b\n' "$C_BLUE" "$*" "$C_RESET"; }
success() { printf '%b%s%b\n' "$C_GREEN" "$*" "$C_RESET"; }
warn() { printf '%b%s%b\n' "$C_YELLOW" "$*" "$C_RESET"; }
error() { printf '%b%s%b\n' "$C_RED" "$*" "$C_RESET" >&2; }
dim() { printf '%b%s%b\n' "$C_DIM" "$*" "$C_RESET"; }

print_banner() {
  printf '%b' "$C_CYAN$C_BOLD"
  cat <<'BANNER'
  ____  _ _ _   ___           _       _     _
 | __ )(_) (_) |_ _|_ __  ___(_) __ _| |__ | |_
 |  _ \| | | |  | || '_ \/ __| |/ _` | '_ \| __|
 | |_) | | | |  | || | | \__ \ | (_| | | | | |_
 |____/|_|_|_| |___|_| |_|___/_|\__, |_| |_|\__|
                                 |___/
BANNER
  printf '%b' "$C_RESET"
  dim '  Bili Insight Docker deployment manager'
  printf '\n'
}

usage() {
  cat <<'USAGE'
用法：deploy.sh [操作] [选项]

操作：
  deploy | update       部署或更新（默认优先 GHCR，失败时回退源码构建）
  restart               重启服务
  status                查看容器和健康状态
  logs [all|backend|frontend]
                        跟踪日志
  uninstall             卸载容器但保留数据库、产物和密钥卷
  purge                 彻底卸载，包括命名卷和部署目录
  self-test             执行脚本内置自检
  help                  显示帮助

选项：
  --dir PATH            部署目录，Linux 默认 /opt/bili-insight
  --host IPV4           监听地址，默认 127.0.0.1；可信局域网可用 0.0.0.0
  --port PORT           Web 端口，默认 8080
  --version TAG         latest 或 v1.2.5 形式的正式版本
  --mode MODE           auto、image 或 source
  -h, --help            显示帮助

不带参数且在交互终端运行时会打开中文管理菜单。
USAGE
}

pause_menu() {
  printf '\n按回车继续...'
  read -e -r _ || true
}

ask() {
  local prompt="$1" default_value="$2" answer
  read -e -r -p "$prompt [$default_value]: " answer || true
  printf '%s' "${answer:-$default_value}"
}

ask_yes_no() {
  local prompt="$1" default_value="$2" answer
  read -e -r -p "$prompt [$default_value]: " answer || true
  answer="$(printf '%s' "${answer:-$default_value}" | tr '[:upper:]' '[:lower:]')"
  case "$answer" in
    y|yes|是|true|1) return 0 ;;
    *) return 1 ;;
  esac
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    error "缺少命令：$1"
    return 1
  }
}

check_requirements() {
  local missing=0
  require_command docker || missing=1
  require_command curl || missing=1
  require_command tar || missing=1
  require_command awk || missing=1
  if ! docker compose version >/dev/null 2>&1; then
    error '缺少 Docker Compose v2：docker compose'
    missing=1
  fi
  if [[ "$missing" -ne 0 ]]; then
    error '请先安装缺失依赖后重新运行。'
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    error 'Docker 服务未运行，请先启动 Docker Engine 或 Docker Desktop。'
    exit 1
  fi
}

validate_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && ((10#$1 >= 1 && 10#$1 <= 65535))
}

validate_ipv4() {
  local value="$1" part
  local -a parts
  local old_ifs="$IFS"
  IFS='.'
  read -r -a parts <<< "$value"
  IFS="$old_ifs"
  [[ "${#parts[@]}" -eq 4 ]] || return 1
  for part in "${parts[@]}"; do
    [[ "$part" =~ ^[0-9]+$ ]] || return 1
    ((10#$part >= 0 && 10#$part <= 255)) || return 1
  done
}

validate_version() {
  [[ "$1" == 'latest' || "$1" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

validate_mode() {
  [[ "$1" == 'auto' || "$1" == 'image' || "$1" == 'source' ]]
}

normalize_deploy_dir() {
  if ! mkdir -p "$DEPLOY_DIR"; then
    error "无法创建部署目录：$DEPLOY_DIR"
    error 'Linux 默认目录位于 /opt；请使用 sudo 运行，或通过 --dir 指定当前用户可写目录。'
    exit 1
  fi
  DEPLOY_DIR="$(cd "$DEPLOY_DIR" && pwd -P)"
  if [[ "$DEPLOY_DIR" == '/' || "$DEPLOY_DIR" == "$HOME" ]]; then
    error "拒绝使用危险部署目录：$DEPLOY_DIR"
    exit 1
  fi
}

app_dir() {
  printf '%s/app' "$DEPLOY_DIR"
}

compose_at() {
  local directory="$1"
  shift
  docker compose \
    --project-directory "$directory" \
    --env-file "$directory/.env" \
    -f "$directory/docker-compose.yml" \
    "$@"
}

download_file() {
  local url="$1" destination="$2"
  curl --fail --show-error --silent --location \
    --retry 3 --connect-timeout 15 \
    "$url" -o "$destination"
}

resolve_version() {
  if [[ "$RELEASE_VERSION" != 'latest' ]]; then
    printf '%s' "$RELEASE_VERSION"
    return 0
  fi
  local final_url version
  final_url="$(curl --fail --show-error --silent --location \
    --retry 3 --connect-timeout 15 \
    --output /dev/null --write-out '%{url_effective}' \
    "https://github.com/$REPOSITORY/releases/latest")"
  version="${final_url##*/}"
  validate_version "$version" || {
    error "无法从 GitHub Latest Release 解析版本：$final_url"
    return 1
  }
  printf '%s' "$version"
}

release_base_url() {
  printf 'https://github.com/%s/releases/download/%s' "$REPOSITORY" "$1"
}

set_env_value() {
  local file="$1" key="$2" value="$3" temp_file
  temp_file="${file}.tmp.$$"
  if [[ -f "$file" ]]; then
    awk -v key="$key" '$0 !~ ("^" key "=") { print }' "$file" > "$temp_file"
  else
    : > "$temp_file"
  fi
  printf '%s=%s\n' "$key" "$value" >> "$temp_file"
  mv "$temp_file" "$file"
}

copy_existing_env() {
  local target="$1" current
  current="$(app_dir)/.env"
  if [[ -f "$current" ]]; then
    cp "$current" "$target"
    return 0
  fi
  return 1
}

load_existing_network_config() {
  local env_file current_host current_port
  env_file="$(app_dir)/.env"
  [[ -f "$env_file" ]] || return 0

  current_host="$(awk -F= '$1 == "WEB_HOST" {value=substr($0, index($0, "=") + 1)} END {print value}' "$env_file")"
  current_port="$(awk -F= '$1 == "WEB_PORT" {value=substr($0, index($0, "=") + 1)} END {print value}' "$env_file")"
  if [[ "$HOST_EXPLICIT" -eq 0 && -n "$current_host" ]]; then
    if validate_ipv4 "$current_host"; then
      WEB_HOST_VALUE="$current_host"
    else
      warn "忽略现有配置中的无效 WEB_HOST：$current_host"
    fi
  fi
  if [[ "$PORT_EXPLICIT" -eq 0 && -n "$current_port" ]]; then
    if validate_port "$current_port"; then
      WEB_PORT_VALUE="$current_port"
    else
      warn "忽略现有配置中的无效 WEB_PORT：$current_port"
    fi
  fi
}

configure_env() {
  local file="$1" version="$2" mode="$3"
  set_env_value "$file" WEB_HOST "$WEB_HOST_VALUE"
  set_env_value "$file" WEB_PORT "$WEB_PORT_VALUE"
  if [[ "$mode" == 'image' ]]; then
    set_env_value "$file" BACKEND_IMAGE "ghcr.io/junwan666/bili-insight-backend:${version}"
    set_env_value "$file" FRONTEND_IMAGE "ghcr.io/junwan666/bili-insight-frontend:${version}"
  else
    set_env_value "$file" BACKEND_IMAGE "bili-insight-backend:local-${version}"
    set_env_value "$file" FRONTEND_IMAGE "bili-insight-frontend:local-${version}"
  fi
}

safe_remove_internal() {
  local target="$1"
  case "$target" in
    "$DEPLOY_DIR"/.stage-*|"$DEPLOY_DIR"/.extract-*|"$DEPLOY_DIR"/.previous|"$DEPLOY_DIR"/.failed)
      rm -rf -- "$target"
      ;;
    *)
      error "拒绝删除非内部路径：$target"
      return 1
      ;;
  esac
}

cleanup_stage() {
  if [[ -n "$ACTIVE_STAGE" && -e "$ACTIVE_STAGE" ]]; then
    safe_remove_internal "$ACTIVE_STAGE" || true
  fi
}
trap cleanup_stage EXIT

new_stage_dir() {
  ACTIVE_STAGE="$DEPLOY_DIR/.stage-$$-$(date +%s)"
  mkdir -p "$ACTIVE_STAGE"
}

prepare_image_stage() {
  local stage="$1" version="$2" base_url
  base_url="$(release_base_url "$version")"
  info "下载 $version Compose 配置..."
  download_file "$base_url/docker-compose.yml" "$stage/docker-compose.yml"
  if ! copy_existing_env "$stage/.env"; then
    download_file "$base_url/ghcr-compose.env" "$stage/.env"
  fi
  configure_env "$stage/.env" "$version" image
  compose_at "$stage" config --quiet
  info "拉取 $version GHCR 镜像..."
  compose_at "$stage" pull
}

prepare_source_stage() {
  local stage="$1" version="$2"
  local archive="$DEPLOY_DIR/.source-${version}-$$.tar.gz"
  local extract_dir="$DEPLOY_DIR/.extract-$$-$(date +%s)"
  local source_root

  mkdir -p "$extract_dir"
  info "下载 $version 源码归档并进行本地构建..."
  download_file "https://github.com/$REPOSITORY/archive/refs/tags/${version}.tar.gz" "$archive"
  tar -xzf "$archive" -C "$extract_dir"
  rm -f -- "$archive"
  source_root=''
  local candidate
  for candidate in "$extract_dir"/*; do
    if [[ -d "$candidate" ]]; then
      source_root="$candidate"
      break
    fi
  done
  if [[ -z "$source_root" || ! -f "$source_root/docker-compose.yml" ]]; then
    safe_remove_internal "$extract_dir"
    error '源码归档结构无效。'
    return 1
  fi
  cp -a "$source_root/." "$stage/"
  safe_remove_internal "$extract_dir"

  if ! copy_existing_env "$stage/.env"; then
    cp "$stage/.env.example" "$stage/.env"
  fi
  configure_env "$stage/.env" "$version" source
  compose_at "$stage" config --quiet
  if ! compose_at "$stage" build --pull; then
    warn '拉取最新基础镜像失败，正在使用本机 Docker 缓存重试构建。'
    compose_at "$stage" build
  fi
}

activate_stage() {
  local stage="$1" current previous failed
  current="$(app_dir)"
  previous="$DEPLOY_DIR/.previous"
  failed="$DEPLOY_DIR/.failed"

  safe_remove_internal "$previous" || true
  safe_remove_internal "$failed" || true
  if [[ -d "$current" ]]; then
    mv "$current" "$previous"
  fi
  mv "$stage" "$current"
  ACTIVE_STAGE=""

  if ! compose_at "$current" up --detach --no-build --force-recreate --wait; then
    error '新版本启动失败，正在恢复上一份部署配置。'
    mv "$current" "$failed"
    if [[ -d "$previous" ]]; then
      mv "$previous" "$current"
      compose_at "$current" up --detach --no-build --wait || true
    fi
    safe_remove_internal "$failed" || true
    return 1
  fi
  safe_remove_internal "$previous" || true
}

get_lan_ip() {
  local ip_value=''
  if command -v ip >/dev/null 2>&1; then
    ip_value="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}' || true)"
  fi
  if [[ -z "$ip_value" ]] && command -v hostname >/dev/null 2>&1; then
    ip_value="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
  fi
  if [[ -z "$ip_value" ]] && command -v ipconfig >/dev/null 2>&1; then
    ip_value="$(ipconfig getifaddr en0 2>/dev/null || true)"
  fi
  printf '%s' "$ip_value"
}

show_access_url() {
  local display_host="$WEB_HOST_VALUE"
  if [[ "$display_host" == '0.0.0.0' ]]; then
    display_host="$(get_lan_ip)"
    display_host="${display_host:-<主机局域网IP>}"
  fi
  success "访问地址：http://${display_host}:${WEB_PORT_VALUE}"
  dim "健康检查：http://${display_host}:${WEB_PORT_VALUE}/healthz"
}

deploy_or_update() {
  check_requirements
  normalize_deploy_dir
  load_existing_network_config
  validate_ipv4 "$WEB_HOST_VALUE" || {
    error "无效监听地址：$WEB_HOST_VALUE"
    exit 1
  }
  validate_port "$WEB_PORT_VALUE" || {
    error "无效端口：$WEB_PORT_VALUE"
    exit 1
  }
  validate_version "$RELEASE_VERSION" || {
    error "无效版本：$RELEASE_VERSION"
    exit 1
  }
  validate_mode "$DEPLOY_MODE" || {
    error "无效部署模式：$DEPLOY_MODE"
    exit 1
  }

  local version stage selected_mode
  version="$(resolve_version)"
  new_stage_dir
  stage="$ACTIVE_STAGE"
  selected_mode="$DEPLOY_MODE"

  if [[ "$DEPLOY_MODE" == 'source' ]]; then
    prepare_source_stage "$stage" "$version"
    selected_mode='source'
  elif prepare_image_stage "$stage" "$version"; then
    selected_mode='image'
  elif [[ "$DEPLOY_MODE" == 'auto' ]]; then
    warn 'GHCR 镜像无法匿名拉取，自动回退到同版本源码构建。'
    safe_remove_internal "$stage"
    ACTIVE_STAGE=''
    new_stage_dir
    stage="$ACTIVE_STAGE"
    prepare_source_stage "$stage" "$version"
    selected_mode='source'
  else
    error 'GHCR 镜像拉取失败。可使用 --mode source 从公开源码构建。'
    return 1
  fi

  activate_stage "$stage"
  printf '%s\n' "$selected_mode" > "$DEPLOY_DIR/.deployment-mode"
  printf '%s\n' "$version" > "$DEPLOY_DIR/.deployment-version"
  success "Bili Insight $version 部署完成，模式：$selected_mode"
  show_access_url
}

require_deployment() {
  normalize_deploy_dir
  if [[ ! -f "$(app_dir)/docker-compose.yml" || ! -f "$(app_dir)/.env" ]]; then
    error "未找到部署配置：$(app_dir)"
    error '请先执行部署。'
    exit 1
  fi
  local env_file current_host current_port
  env_file="$(app_dir)/.env"
  current_host="$(awk -F= '$1 == "WEB_HOST" {value=substr($0, index($0, "=") + 1)} END {print value}' "$env_file")"
  current_port="$(awk -F= '$1 == "WEB_PORT" {value=substr($0, index($0, "=") + 1)} END {print value}' "$env_file")"
  if [[ -n "$current_host" ]]; then
    WEB_HOST_VALUE="$current_host"
  fi
  if [[ -n "$current_port" ]]; then
    WEB_PORT_VALUE="$current_port"
  fi
}

restart_services() {
  check_requirements
  require_deployment
  compose_at "$(app_dir)" restart
  compose_at "$(app_dir)" up --detach --no-build --wait
  success '服务已重启。'
  show_access_url
}

show_status() {
  check_requirements
  require_deployment
  compose_at "$(app_dir)" ps
  printf '\n'
  local health_host="$WEB_HOST_VALUE"
  if [[ "$health_host" == '0.0.0.0' ]]; then
    health_host='127.0.0.1'
  fi
  if curl --fail --silent --show-error "http://${health_host}:${WEB_PORT_VALUE}/healthz" >/dev/null; then
    success '端到端健康检查通过。'
  else
    warn '健康检查未通过，请查看日志。'
  fi
}

show_logs() {
  check_requirements
  require_deployment
  case "$LOG_TARGET" in
    backend|frontend) compose_at "$(app_dir)" logs --follow --tail=200 "$LOG_TARGET" ;;
    all) compose_at "$(app_dir)" logs --follow --tail=200 ;;
    *) error "未知日志目标：$LOG_TARGET"; exit 1 ;;
  esac
}

uninstall_keep_data() {
  check_requirements
  require_deployment
  warn '将移除容器和网络，但保留数据库、产物、Cookie 密钥及部署文件。'
  if [[ -t 0 ]] && ! ask_yes_no '确认继续？' 'N'; then
    info '已取消。'
    return 0
  fi
  compose_at "$(app_dir)" down --remove-orphans
  success '容器已卸载，数据卷和部署文件已保留。'
}

purge_all() {
  check_requirements
  require_deployment
  error '危险操作：将删除容器、bili-insight-runtime、bili-insight-secrets 和部署目录。'
  local confirmation=''
  if [[ -t 0 ]]; then
    read -e -r -p '请输入 DELETE 确认彻底删除：' confirmation || true
  fi
  if [[ "$confirmation" != 'DELETE' ]]; then
    info '确认文本不匹配，已取消。'
    return 0
  fi
  compose_at "$(app_dir)" down --volumes --remove-orphans
  local parent_dir base_name
  parent_dir="$(dirname "$DEPLOY_DIR")"
  base_name="$(basename "$DEPLOY_DIR")"
  if [[ "$DEPLOY_DIR" == '/' || "$DEPLOY_DIR" == "$HOME" || -z "$base_name" ]]; then
    error "拒绝删除危险路径：$DEPLOY_DIR"
    return 1
  fi
  (cd "$parent_dir" && rm -rf -- "$base_name")
  success 'Bili Insight 容器、数据卷和部署目录已彻底删除。'
}

self_test() {
  local test_dir env_file original_deploy_dir original_host original_port
  local original_host_explicit original_port_explicit
  test_dir="$(mktemp -d "${TMPDIR:-/tmp}/bili-insight-deploy-test.XXXXXX")"
  env_file="$test_dir/.env"
  printf 'WEB_HOST=127.0.0.1\nWEB_PORT=8080\n' > "$env_file"
  set_env_value "$env_file" WEB_PORT 18080
  set_env_value "$env_file" BACKEND_IMAGE test-backend:v1
  grep -qx 'WEB_PORT=18080' "$env_file"
  grep -qx 'BACKEND_IMAGE=test-backend:v1' "$env_file"
  [[ "$(grep -c '^WEB_PORT=' "$env_file")" -eq 1 ]]
  validate_port 8080
  ! validate_port 70000
  validate_ipv4 127.0.0.1
  ! validate_ipv4 999.0.0.1
  validate_version latest
  validate_version v1.2.5
  ! validate_version main
  validate_mode auto
  validate_mode source
  if [[ "$(uname -s 2>/dev/null || true)" == 'Linux' ]]; then
    [[ "$PLATFORM_DEFAULT_DEPLOY_DIR" == '/opt/bili-insight' ]]
  fi
  original_host="$WEB_HOST_VALUE"
  original_port="$WEB_PORT_VALUE"
  original_host_explicit="$HOST_EXPLICIT"
  original_port_explicit="$PORT_EXPLICIT"
  WEB_HOST_VALUE='0.0.0.0'
  WEB_PORT_VALUE='18080'
  configure_env "$env_file" v1.2.5 source
  grep -qx 'WEB_HOST=0.0.0.0' "$env_file"
  grep -qx 'WEB_PORT=18080' "$env_file"
  grep -qx 'BACKEND_IMAGE=bili-insight-backend:local-v1.2.5' "$env_file"
  grep -qx 'FRONTEND_IMAGE=bili-insight-frontend:local-v1.2.5' "$env_file"
  WEB_HOST_VALUE="$original_host"
  WEB_PORT_VALUE="$original_port"
  original_deploy_dir="$DEPLOY_DIR"
  DEPLOY_DIR="$test_dir/deploy"
  normalize_deploy_dir
  new_stage_dir
  [[ -n "$ACTIVE_STAGE" && -d "$ACTIVE_STAGE" ]]
  safe_remove_internal "$ACTIVE_STAGE"
  ACTIVE_STAGE=''
  mkdir -p "$(app_dir)"
  printf 'WEB_HOST=0.0.0.0\nWEB_PORT=19090\n' > "$(app_dir)/.env"
  WEB_HOST_VALUE='127.0.0.1'
  WEB_PORT_VALUE='8080'
  HOST_EXPLICIT=0
  PORT_EXPLICIT=0
  load_existing_network_config
  [[ "$WEB_HOST_VALUE" == '0.0.0.0' ]]
  [[ "$WEB_PORT_VALUE" == '19090' ]]
  WEB_HOST_VALUE='127.0.0.1'
  WEB_PORT_VALUE='18080'
  HOST_EXPLICIT=1
  PORT_EXPLICIT=1
  load_existing_network_config
  [[ "$WEB_HOST_VALUE" == '127.0.0.1' ]]
  [[ "$WEB_PORT_VALUE" == '18080' ]]
  DEPLOY_DIR="$original_deploy_dir"
  WEB_HOST_VALUE="$original_host"
  WEB_PORT_VALUE="$original_port"
  HOST_EXPLICIT="$original_host_explicit"
  PORT_EXPLICIT="$original_port_explicit"
  rm -rf -- "$test_dir"
  success 'deploy.sh 自检通过。'
}

interactive_deploy() {
  DEPLOY_DIR="$(ask '部署目录' "$DEPLOY_DIR")"
  normalize_deploy_dir
  load_existing_network_config
  printf '访问范围：1) 仅本机  2) 可信局域网\n'
  local access_choice default_access_choice='1'
  if [[ "$WEB_HOST_VALUE" == '0.0.0.0' ]]; then
    default_access_choice='2'
  fi
  access_choice="$(ask '请选择' "$default_access_choice")"
  if [[ "$access_choice" == '2' ]]; then
    WEB_HOST_VALUE='0.0.0.0'
  else
    WEB_HOST_VALUE='127.0.0.1'
  fi
  HOST_EXPLICIT=1
  WEB_PORT_VALUE="$(ask 'Web 端口' "$WEB_PORT_VALUE")"
  PORT_EXPLICIT=1
  RELEASE_VERSION="$(ask '版本（latest 或 vX.Y.Z）' "$RELEASE_VERSION")"
  printf '部署模式：1) 自动  2) 仅镜像  3) 源码构建\n'
  local mode_choice
  mode_choice="$(ask '请选择' '1')"
  case "$mode_choice" in
    2) DEPLOY_MODE='image' ;;
    3) DEPLOY_MODE='source' ;;
    *) DEPLOY_MODE='auto' ;;
  esac
  deploy_or_update
}

main_menu() {
  while true; do
    print_banner
    printf '当前部署目录：%s\n\n' "$DEPLOY_DIR"
    printf '  %b1%b) 部署 / 更新\n' "$C_GREEN$C_BOLD" "$C_RESET"
    printf '  %b2%b) 重启服务\n' "$C_BLUE$C_BOLD" "$C_RESET"
    printf '  %b3%b) 查看状态\n' "$C_BLUE$C_BOLD" "$C_RESET"
    printf '  %b4%b) 查看全部日志\n' "$C_BLUE$C_BOLD" "$C_RESET"
    printf '  %b5%b) 卸载但保留数据\n' "$C_YELLOW$C_BOLD" "$C_RESET"
    printf '  %b6%b) 彻底卸载\n' "$C_RED$C_BOLD" "$C_RESET"
    printf '  0) 退出\n\n'
    local choice
    read -e -r -p '请选择操作：' choice || true
    case "$choice" in
      1) interactive_deploy; pause_menu ;;
      2) restart_services; pause_menu ;;
      3) show_status; pause_menu ;;
      4) LOG_TARGET='all'; show_logs ;;
      5) uninstall_keep_data; pause_menu ;;
      6) purge_all; pause_menu ;;
      0) return 0 ;;
      *) warn '无效选项。'; pause_menu ;;
    esac
  done
}

parse_arguments() {
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      deploy|install|update|restart|status|uninstall|purge|self-test|help)
        ACTION="$1"
        shift
        ;;
      logs)
        ACTION='logs'
        if [[ "${2:-}" == 'all' || "${2:-}" == 'backend' || "${2:-}" == 'frontend' ]]; then
          LOG_TARGET="$2"
          shift
        fi
        shift
        ;;
      --dir)
        DEPLOY_DIR="${2:?--dir 需要路径}"
        shift 2
        ;;
      --host)
        WEB_HOST_VALUE="${2:?--host 需要 IPv4 地址}"
        HOST_EXPLICIT=1
        shift 2
        ;;
      --port)
        WEB_PORT_VALUE="${2:?--port 需要端口}"
        PORT_EXPLICIT=1
        shift 2
        ;;
      --version)
        RELEASE_VERSION="${2:?--version 需要版本}"
        shift 2
        ;;
      --mode)
        DEPLOY_MODE="${2:?--mode 需要模式}"
        shift 2
        ;;
      -h|--help)
        ACTION='help'
        shift
        ;;
      *)
        error "未知参数：$1"
        usage
        exit 2
        ;;
    esac
  done
}

main() {
  parse_arguments "$@"
  case "$ACTION" in
    deploy|install|update) deploy_or_update ;;
    restart) restart_services ;;
    status) show_status ;;
    logs) show_logs ;;
    uninstall) uninstall_keep_data ;;
    purge) purge_all ;;
    self-test) self_test ;;
    help) usage ;;
    '')
      if [[ -t 0 ]]; then
        main_menu
      else
        usage
      fi
      ;;
  esac
}

main "$@"
