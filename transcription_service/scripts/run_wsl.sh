#!/usr/bin/env bash
set -euo pipefail

service_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
repo_env_file="${service_dir}/../.env"

if [[ -f "${repo_env_file}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${repo_env_file}"
  set +a
fi

venv_dir="${TRANSCRIPTION_VENV_DIR:-${service_dir}/.venv-wsl}"
data_dir="${TRANSCRIPTION_DATA_DIR:-${service_dir}/../data}"
host="${TRANSCRIPTION_HOST:-127.0.0.1}"
port="${TRANSCRIPTION_PORT:-8012}"

if [[ ! -x "${venv_dir}/bin/python" ]]; then
  echo "Missing ${venv_dir}. Create the WSL virtual environment first." >&2
  exit 1
fi

cublas_dir="$("${venv_dir}/bin/python" -c 'import nvidia.cublas.lib; print(next(iter(nvidia.cublas.lib.__path__)))')"
cudnn_dir="$("${venv_dir}/bin/python" -c 'import nvidia.cudnn.lib; print(next(iter(nvidia.cudnn.lib.__path__)))')"

export TRANSCRIPTION_DATA_DIR="${data_dir}"
export TRANSCRIPTION_LOCAL_WORKER_ENABLED="${TRANSCRIPTION_LOCAL_WORKER_ENABLED:-true}"
export HF_HOME="${HF_HOME:-${data_dir}/models}"
export HF_HUB_DISABLE_TELEMETRY=1
export LD_LIBRARY_PATH="${cublas_dir}:${cudnn_dir}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${service_dir}"
exec "${venv_dir}/bin/python" -m uvicorn app.main:app --host "${host}" --port "${port}"
