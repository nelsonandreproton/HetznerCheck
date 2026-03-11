# HetznerCheck

Monitor de VM para Hetzner Cloud. Corre como container Docker na própria VM e envia alertas e resumos diários para o Telegram.

## O que monitoriza

| Métrica | Threshold padrão |
|---|---|
| CPU | > 85% |
| Load average | > 2× nº de CPUs |
| RAM | > 90% |
| Swap | > 50% |
| Disco | > 85% por partição |
| Tentativas SSH falhadas | > 20 por hora |
| Containers Docker parados | qualquer |
| Containers Docker unhealthy | qualquer |
| Reinício inesperado do servidor | sempre |
| Processos zombie | > 5 |

**Alertas** são enviados quando um threshold é ultrapassado, com cooldown configurável (padrão: 60 min) para evitar spam.

**Resumo diário** com o estado geral da máquina, enviado à hora configurada (padrão: 08:00).

## Pré-requisitos

- Docker + Docker Compose instalados na VM
- Bot Telegram criado via [@BotFather](https://t.me/BotFather)
- Chat ID do teu chat ou grupo Telegram

## Instalação

### 1. Clonar o repositório na VM

```bash
git clone <repo-url> /opt/hetzner-monitor
cd /opt/hetzner-monitor
```

### 2. Configurar credenciais

```bash
cp .env.example .env
nano .env
```

Preenche o `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_CHAT_ID=-100xxxxxxxxxx
```

> O `TELEGRAM_CHAT_ID` é negativo para grupos (começa com `-100`). Para chat privado, é um número positivo. Podes obter o ID enviando uma mensagem ao bot e consultando `https://api.telegram.org/bot<TOKEN>/getUpdates`.

### 3. Ajustar thresholds (opcional)

Edita o `config.yml` para ajustar os limites de alerta, hora do resumo diário, partições a monitorizar, etc.

### 4. Iniciar

```bash
docker compose up -d --build
```

### 5. Verificar logs

```bash
docker compose logs -f
```

Se tudo estiver correto, recebes uma mensagem no Telegram: **"✅ Monitor iniciado"**.

## Deploy de atualizações

```bash
bash deploy.sh
```

O script faz `git pull`, rebuilda a imagem e reinicia o container.

## Configuração

Todas as opções estão no `config.yml`:

```yaml
thresholds:
  cpu_percent: 85            # alerta se CPU > 85%
  cpu_load_multiplier: 2.0   # alerta se load > 2× nº CPUs
  memory_percent: 90
  swap_percent: 50
  disk_percent: 85
  ssh_failures_per_hour: 20
  zombie_count: 5

alerts:
  cooldown_minutes: 60       # tempo mínimo entre alertas do mesmo tipo

schedule:
  check_interval_seconds: 300   # verificar a cada 5 minutos
  daily_summary_time: "08:00"   # hora do resumo diário (TZ do container)

disk:
  check_paths:
    - "/rootfs"              # raiz do host
    # - "/rootfs/data"       # adicionar se tiveres partições separadas

docker:
  ignore_containers: []      # containers a excluir das verificações
```

### Múltiplas partições

Se tiveres discos separados (ex: `/data`), adiciona em `config.yml`:

```yaml
disk:
  check_paths:
    - "/rootfs"
    - "/rootfs/data"
```

### Timezone

A hora do resumo diário usa a timezone definida no `docker-compose.yml`:

```yaml
environment:
  - TZ=Europe/Lisbon
```

## Estrutura do projeto

```
HetznerCheck/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config.yml            ← thresholds e agendamento
├── .env                  ← credenciais (não commitar)
├── .env.example
├── .gitignore
├── deploy.sh
└── monitor/
    ├── main.py           ← loop principal e scheduler
    ├── collectors.py     ← recolha de métricas do sistema
    ├── checker.py        ← comparação com thresholds e gestão de cooldown
    ├── telegram.py       ← envio de mensagens e formatação
    └── utils.py          ← utilitários partilhados
```

## Notas de segurança

Este container requer privilégios elevados para aceder às métricas reais do host:

| Permissão | Motivo |
|---|---|
| `pid: host` | Ver processos e métricas reais do host (CPU, RAM, load) |
| `network_mode: host` | Ver tráfego de rede real do host |
| `/:/rootfs:ro` | Ler uso de disco das partições do host (só leitura) |
| `/var/log:ro` | Ler auth.log para detetar tentativas SSH |
| `/var/run/docker.sock:ro` | Consultar estado dos containers via API Docker |

> **Atenção:** O acesso ao Docker socket (`docker.sock`) é equivalente a acesso root no host via API Docker. Garante que apenas containers de confiança o montam.

O `.env` com as credenciais do Telegram **nunca deve ser commitado**. Está incluído no `.gitignore`.
