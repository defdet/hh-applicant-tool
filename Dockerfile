FROM python:3.13-slim

# Системные зависимости
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      gcc \
      libc6-dev \
      procps \
      cron \
      dos2unix \
      tzdata \
      less && \
    rm -rf /var/lib/apt/lists/*

# Настройка пользователя
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID docker && \
    useradd -u $UID -g docker -m -s /bin/bash docker

WORKDIR /app

COPY pyproject.toml poetry.lock* .

# 1. Cтавим playwright, браузер и системные зависимости (этот тяжелый слой теперь закэшируется)
RUN touch README.md && \
    pip install --no-cache-dir playwright && \
    playwright install-deps chromium && \
    su docker -c "playwright install chromium"

# 2. Claude Code CLI — им пишутся сопроводительные письма (`--ai-provider claude`).
#    Ставится от пользователя docker, потому что /root недоступен всем остальным.
#    Отключается через `--build-arg INSTALL_CLAUDE=0`
ARG INSTALL_CLAUDE=1
# claude.ai и api.anthropic.com доступны не из любой страны. Прокси на хосте
# виден изнутри как host.docker.internal (см. build.extra_hosts в compose).
# Значение попадает в историю образа, так что это место не для паролей
ARG CLAUDE_BUILD_PROXY=
ENV PATH="/home/docker/.local/bin:${PATH}"
# Каталог с авторизацией claude. Лежит в примонтированном config, чтобы логин
# переживал пересоздание контейнера
ENV CLAUDE_CONFIG_DIR=/app/config/claude
# Из закрытых регионов claude.ai отдает HTML-заглушку с кодом 200, поэтому
# скачанный установщик проверяется на шебанг: curl -f такое не ловит
RUN if [ "$INSTALL_CLAUDE" = "1" ]; then \
      export HTTP_PROXY="$CLAUDE_BUILD_PROXY" HTTPS_PROXY="$CLAUDE_BUILD_PROXY"; \
      curl -fsSL https://claude.ai/install.sh -o /tmp/claude-install.sh || exit 1; \
      if ! head -1 /tmp/claude-install.sh | grep -q '^#!'; then \
        echo "claude.ai отдал заглушку вместо установщика. Укажите прокси:"; \
        echo "  CLAUDE_BUILD_PROXY=http://host.docker.internal:1081 docker compose build"; \
        echo "или соберите образ без claude:"; \
        echo "  docker compose build --build-arg INSTALL_CLAUDE=0"; \
        exit 1; \
      fi; \
      chown docker /tmp/claude-install.sh; \
      su docker -c "HTTP_PROXY='$CLAUDE_BUILD_PROXY' HTTPS_PROXY='$CLAUDE_BUILD_PROXY' bash /tmp/claude-install.sh" || exit 1; \
      rm -f /tmp/claude-install.sh; \
      su docker -c "claude --version" || exit 1; \
    fi

# 3. Теперь копируем исходный код
COPY src ./src

# 4. Устанавливаем саму утилиту и остальные зависимости
#    Подготовка cron
RUN pip install --no-cache-dir -e '.[playwright,pillow]' && \
    touch /var/log/cron.log && chown docker:docker /var/log/cron.log && \
    mkdir -p ./config && chown -R docker:docker ./config

# Копируем остальное (эти файлы мешают кешированию последующих слоев)
COPY --chmod=755 crontab startup.sh .

# Запускаем cron и читаем лог
# cron не видит переменные окружения, переданные главному процессу, точнее
# он начинает новую сессию, где тот же $CONFIG_DIR пуст
CMD printenv | grep -E '^(CONFIG_DIR|HH_PROFILE_ID|CLAUDE_[A-Z_]+|ANTHROPIC_[A-Z_]+|TZ|(HTTP|HTTPS|ALL|NO)_PROXY)=' | sort -u > /etc/environment && \
    mkdir -p "$CLAUDE_CONFIG_DIR" && \
    chown -R docker:docker ./config && \
    dos2unix -n ./crontab /tmp/crontab && \
    crontab -u docker /tmp/crontab && \
    cron && \
    tail -f /var/log/cron.log
