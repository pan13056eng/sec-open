#!/bin/bash
# 双击本文件：启用/重新启用定时抓取（每天 0/6/12/18:05，开机后补跑一次）
# 改过 com.user.usstockintel.plist 之后，双击一次即可生效。
cd "$(dirname "$0")" || exit 1

launchctl bootout gui/$(id -u)/com.user.usstockintel 2>/dev/null
cp com.user.usstockintel.plist ~/Library/LaunchAgents/ || exit 1

if launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.user.usstockintel.plist 2>/dev/null; then
    echo "✅ 定时抓取已启用：每天 00:05 / 06:05 / 12:05 / 18:05，开机后补跑一次"
else
    echo "⚠️  装载失败，试试旧语法…"
    launchctl unload ~/Library/LaunchAgents/com.user.usstockintel.plist 2>/dev/null
    launchctl load ~/Library/LaunchAgents/com.user.usstockintel.plist \
        && echo "✅ 已启用" || echo "❌ 仍未启用，请把上面的报错发给我"
fi

echo
echo "当前状态："
launchctl list | grep -i usstock || echo "（未找到，可能未启用）"
echo
echo "按任意键关闭…"; read -r _
