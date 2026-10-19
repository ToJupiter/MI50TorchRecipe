#!/bin/bash
# usage: ./monitor.sh [seconds]
C=/sys/class/drm/card1/device
H=$C/hwmon/hwmon1
n=${1:-20}
for ((i=0;i<n;i++)); do
  sclk=$(awk '/\*/{print $2}' "$C/pp_dpm_sclk" 2>/dev/null)
  mclk=$(awk '/\*/{print $2}' "$C/pp_dpm_mclk" 2>/dev/null)
  echo "$(date +%H:%M:%S) busy=$(cat $C/gpu_busy_percent)% sclk=$sclk mclk=$mclk power=$(($(cat $H/power1_input)/1000000))W temp=$(($(cat $H/temp2_input)/1000))C"
  sleep 1
done
