# Stage 1: Base image with system dependencies
FROM python:3.12-slim AS base

ARG TZ
ENV TZ="$TZ"

# Pin Node.js version for consistency
ARG NODE_MAJOR=20

# Install Node.js and basic development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  gnupg \
  && mkdir -p /etc/apt/keyrings \
  && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
  && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
  && apt-get update \
  && apt-get install -y --no-install-recommends \
  nodejs \
  less \
  git \
  procps \
  sudo \
  fzf \
  zsh \
  man-db \
  unzip \
  gh \
  jq \
  nano \
  vim \
  wget \
  build-essential \
  libportaudio2 \
  ffmpeg \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Create gaia user
ARG USERNAME=gaia
RUN useradd -m -s /bin/bash $USERNAME && \
  echo "$USERNAME ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$USERNAME && \
  chmod 0440 /etc/sudoers.d/$USERNAME

# Persist bash history
RUN SNIPPET="export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  && mkdir /commandhistory \
  && touch /commandhistory/.bash_history \
  && chown -R $USERNAME /commandhistory

# Set `DEVCONTAINER` environment variable to help with orientation
ENV DEVCONTAINER=true

# Create workspace and config directories and set permissions
RUN mkdir -p /workspace /home/$USERNAME/.claude && \
  chown -R $USERNAME:$USERNAME /workspace /home/$USERNAME/.claude

WORKDIR /workspace

# Stage 2: Development tools
FROM base AS devtools

ARG GIT_DELTA_VERSION=0.18.2
RUN ARCH=$(dpkg --print-architecture) && \
  wget "https://github.com/dandavison/delta/releases/download/${GIT_DELTA_VERSION}/git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  dpkg -i "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb" && \
  rm "git-delta_${GIT_DELTA_VERSION}_${ARCH}.deb"

USER gaia

# Add ~/.local/bin and ~/.claude/local to PATH so gaia CLI and claude binary are discoverable
ENV PATH=$PATH:/home/gaia/.local/bin:/home/gaia/.claude/local

# Stage 3: Final image with shell configuration
FROM devtools AS final

USER gaia

# Set the default shell to zsh rather than sh
ENV SHELL=/bin/zsh

# Set the default editor and visual (VS Code CLI with --wait for proper integration)
ENV EDITOR="code --wait"
ENV VISUAL="code --wait"

# Default powerline10k theme
ARG ZSH_IN_DOCKER_VERSION=1.2.0
RUN sh -c "$(wget -O- https://github.com/deluan/zsh-in-docker/releases/download/v${ZSH_IN_DOCKER_VERSION}/zsh-in-docker.sh)" -- \
  -p git \
  -p fzf \
  -p python \
  -a "source /usr/share/doc/fzf/examples/key-bindings.zsh" \
  -a "source /usr/share/doc/fzf/examples/completion.zsh" \
  -a "export PROMPT_COMMAND='history -a' && export HISTFILE=/commandhistory/.bash_history" \
  -a "export PATH=\"\$HOME/.local/bin:\$HOME/.claude/local:\$PATH\"" \
  -x
