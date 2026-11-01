#!/bin/bash
# DeerFlow 持久开发容器：仅保活 + sshd。参数经 scripts/dev-vm.sh 管理，不在容器 env。
export PATH="/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH"
mkdir -p /run/sshd /root/.ssh
# SSH 公钥由宿主只读挂载到 /tmp/ak，此处复制为 root 属主（满足 sshd StrictModes）
if [ -f /tmp/ak ]; then cp /tmp/ak /root/.ssh/authorized_keys && chown root:root /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys; fi
command -v sshd >/dev/null && /usr/sbin/sshd
exec sleep infinity
