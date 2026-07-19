# fix-compose.ps1 - 添加 hive-env.sh 和 hbase-env.sh 挂载

$file = "C:\Users\feng\aiops-ha\docker-compose.yml"
$content = Get-Content $file -Raw

# 为 hadoop01 添加 hive-env.sh 挂载（在 hive-site.xml 之前）
$content = $content -replace '(- ./config/hive/hive-site.xml:/opt/hive/conf/hive-site.xml:ro)', '- ./config/hive/hive-env.sh:/opt/hive/conf/hive-env.sh:ro
      - ./config/hive/hive-site.xml:/opt/hive/conf/hive-site.xml:ro'

# 为 hadoop01 添加 hbase-env.sh 挂载（在 hbase-site.xml 之前）
$content = $content -replace '(- ./config/hbase/hbase-site.xml:/opt/hbase/conf/hbase-site.xml:ro)', '- ./config/hbase/hbase-env.sh:/opt/hbase/conf/hbase-env.sh:ro
      - ./config/hbase/hbase-site.xml:/opt/hbase/conf/hbase-site.xml:ro'

Set-Content $file $content -NoNewline
Write-Host "docker-compose.yml 已更新"
