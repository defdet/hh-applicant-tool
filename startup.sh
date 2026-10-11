#!/bin/bash
echo "[$(date)] Running startup tasks..."

# echo "Current user: $(whoami)"
# echo "$CONFIG_DIR"

# Те же флаги, что и в crontab (cron экспортирует HH_FLAGS в окружение)
# Выполняем цепочку
/usr/local/bin/python -m hh_applicant_tool $HH_FLAGS refresh-token
/usr/local/bin/python -m hh_applicant_tool $HH_FLAGS update-resumes
/usr/local/bin/python -m hh_applicant_tool $HH_FLAGS apply-vacancies

echo "[$(date)] Startup tasks finished."
