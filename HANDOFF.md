# Polymarket Radar — Handoff Document
_Last updated: 2026-05-20_

---

## Статус: ✅ РАБОТАЕТ

Pipeline полностью функционирует. За последние 12 часов пришло 3 MEDIUM сигнала на спортивные рынки (FIFA World Cup). Пример алерта:

```
⚠️ MEDIUM SIGNAL — Score 36/100
Market: Will Jordan win the 2026 FIFA World Cup?
• Notable entry: ~$5,155
• Wallet funded +$5,188 USDC ~25 min before trade
• Wallet appears new / limited history (only 1 prior trades)
Trader: 0xe962ea9a...
```

---

## Инфраструктура

### VPS
- **IP:** `2.26.105.176`
- **OS:** Ubuntu, 4GB RAM + 2GB swap, 30GB disk
- **SSH:** `ssh root@2.26.105.176`

### Локальные алиасы
```bash
alias kv='kubectl --kubeconfig ~/.kube/config-vps'
alias kl='kubectl --kubeconfig ~/.kube/config-local'
```

### Kubeconfig
- VPS: `~/.kube/config-vps`
- Локальный: `~/.kube/config-local`

---

## Что задеплоено

| Namespace | Компонент | Статус |
|-----------|-----------|--------|
| `ingress-nginx` | nginx ingress controller | ✅ |
| `cert-manager` | cert-manager + ClusterIssuer | ✅ |
| `argocd` | ArgoCD | ✅ |
| `kafka` | Strimzi + kafka-cluster (Zookeeper mode) | ✅ |
| `monitoring` | Prometheus + Grafana | ✅ |
| `vpn` | WireGuard + vk-turn-proxy | ✅ |
| `radar` | collector + scorer + notifier + radardb | ✅ |

### UI
- ArgoCD: `http://argocd.2.26.105.176.nip.io`
- Grafana: `http://grafana.2.26.105.176.nip.io`

```bash
# Пароли
kv -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d && echo
kv -n monitoring get secret monitoring-grafana -o jsonpath="{.data.admin-password}" | base64 -d && echo
```

---

## Репозиторий

**GitHub:** `https://github.com/omgoo191/polymarket-scanner` (приватный)

### Структура
```
polymarket-scanner/
├── src/
│   ├── adapters/
│   │   ├── polymarket.py      # REST + data-api адаптер
│   │   └── polygonscan.py     # USDC watcher (Etherscan V2 API, chainid=137)
│   ├── core/
│   │   ├── scorer.py          # Категорийный scoring (politics/sports/crypto)
│   │   ├── summarizer.py      # Alert text с category emoji
│   │   └── metrics.py         # Prometheus метрики
│   ├── db/
│   │   ├── models.py
│   │   ├── session.py
│   │   └── repository.py
│   ├── kafka/
│   │   ├── producer.py
│   │   └── consumer.py
│   ├── services/
│   │   ├── collector.py       # Сбор → raw-trades (каждые 15 сек)
│   │   ├── scorer_service.py  # raw-trades → scored-trades
│   │   └── notifier_service.py # scored-trades → Telegram
│   ├── notifications/
│   │   └── telegram.py
│   └── main.py                # Entry point: all|collector|scorer|notifier
├── infra/
│   ├── ansible/
│   │   ├── inventory.ini
│   │   ├── group_vars/all.yml
│   │   └── playbooks/
│   │       ├── 01_base.yml
│   │       ├── 02_k3s.yml
│   │       └── 03_argocd.yml
│   ├── helm/
│   │   └── radar/
│   │       ├── Chart.yaml
│   │       ├── values.yaml
│   │       └── templates/
│   │           ├── collector.yaml   # RADAR_MODE=collector
│   │           ├── scorer.yaml      # RADAR_MODE=scorer
│   │           ├── notifier.yaml    # RADAR_MODE=notifier
│   │           ├── service.yaml
│   │           ├── secret.yaml
│   │           └── postgres.yaml
│   └── k8s/
│       ├── cluster-issuer.yaml
│       ├── kafka-cluster.yaml
│       ├── kafka-topics.yaml        # raw-trades, scored-trades
│       ├── ingress-argocd.yaml
│       ├── ingress-grafana.yaml
│       └── vpn/
│           ├── wireguard.yaml
│           └── vk-turn-proxy.yaml
├── Dockerfile
├── docker-compose.yml
├── docker-entrypoint.sh             # pg_isready → migrate → python src/main.py ${RADAR_MODE:-all}
├── requirements.txt
└── .github/workflows/ci.yml        # build → push ghcr.io → update values.yaml tag → push
```

---

## CI/CD Pipeline

```
git push main
    → GitHub Actions
        → docker build
        → push ghcr.io/omgoo191/radar:sha-<hash>
        → update infra/helm/radar/values.yaml (image tag)
        → git push
            → ArgoCD detects change
                → deploy to k3s
```

**Образ:** `ghcr.io/omgoo191/radar`

---

## Kafka

| Топик | Producer | Consumer | Retention |
|-------|----------|----------|-----------|
| `raw-trades` | collector | scorer | 24h |
| `scored-trades` | scorer | notifier | 24h |

**Bootstrap:** `kafka-cluster-kafka-bootstrap.kafka.svc.cluster.local:9092`

---

## Scoring Logic

### Категории рынков (из config.yaml)

| Категория | Keywords примеры | Size medium | Size large | Score MEDIUM | Score STRONG |
|-----------|-----------------|-------------|------------|--------------|--------------|
| politics | election, president, sanctions | $3k | $10k | 35 | 50 |
| sports | nba, nhl, world cup, finals | $15k | $50k | 40 | 60 |
| crypto | bitcoin, ethereum, etf | $30k | $100k | 45 | 65 |

### Feature weights
| Feature | Max pts | Триггер |
|---------|---------|---------|
| SizeScore | 30 | размер ставки |
| TimingScore | 20 | близость к дедлайну |
| WalletHistoryScore | 15 | новый кошелёк |
| FundingScore | 15 | USDC funding перед трейдом |
| ImpactScore | 10 | большой трейд, малый price impact |
| ClusterScore | 10 | несколько кошельков с одного источника |

---

## Polygonscan / Etherscan API

**Важно:** Polygonscan V1 deprecated. Используем Etherscan V2:
- URL: `https://api.etherscan.io/v2/api`
- Параметр: `chainid=137` (Polygon)
- Все запросы в `src/adapters/polygonscan.py` используют этот формат

---

## Polling

- Collector цикл: **каждые 15 секунд** (data-api возвращает ~17 сек трейдов в 500 записях)
- Market refresh: каждые 10 циклов (2.5 минуты)
- Funding lookback: 180 минут

---

## Полезные команды

```bash
# Логи
kv logs -n radar deployment/radar-collector --tail=50 -f
kv logs -n radar deployment/radar-scorer --tail=50 -f
kv logs -n radar deployment/radar-notifier --tail=50 -f

# Статус подов
kv get pods -n radar
kv get pods -A

# БД
kv exec -n radar deployment/radardb -- psql -U radar -d radardb -c "SELECT COUNT(*), MAX(timestamp) FROM trades;"
kv exec -n radar deployment/radardb -- psql -U radar -d radardb -c "SELECT COUNT(*) FROM alerts;"

# Перезапуск
kv rollout restart deployment/radar-collector -n radar
kv rollout restart deployment/radar-scorer -n radar
kv rollout restart deployment/radar-notifier -n radar

# Диск и память VPS
ssh root@2.26.105.176 df -h /
ssh root@2.26.105.176 free -h

# Очистить evicted поды
kubectl get pods -A --kubeconfig ~/.kube/config-vps | grep Evicted | awk '{print $1, $2}' | xargs -n2 kubectl delete pod -n --kubeconfig ~/.kube/config-vps
```

---

## VPN

- WireGuard запущен в k3s (`hostNetwork: true`, `privileged: true`)
- vk-turn-proxy туннелирует через VK TURN серверы (обход блокировки WG)
- VK звонок: `https://vk.ru/call/join/_qoHZb296l5GKytNvupKHL5MouKJyrnWzCdoxzLvWNw`

**Linux клиент:**
```bash
./client -listen 127.0.0.1:9000 -peer 2.26.105.176:56000 -vk-link <ссылка>
```

**WireGuard конфиг для клиента:**
- Endpoint: `127.0.0.1:9000`
- MTU: `1280`
- AllowedIPs: исключить VK диапазон `155.212.192.0/20`

---

## Что не сделано (backlog)

- [ ] Grafana дашборд с кастомными метриками radar (trades/alerts per hour, score distribution)
- [ ] Loki для централизованных логов
- [ ] TLS сертификаты (нужен свой домен с DNS управлением)
- [ ] GitHub webhook → ArgoCD для мгновенного деплоя (сейчас 3 мин polling)
- [ ] CronJob для очистки старых записей в БД (сейчас не чистится)
- [ ] Kafka рефакторинг на KRaft (убрать Zookeeper, освободить ~160MB RAM)
- [ ] Wallet age данные — сейчас Polymarket использует proxy wallets которые всегда новые, нужна альтернативная метрика
- [ ] Тест на реальных инсайд-событиях (дождаться крупного политического события)

---

## Известные проблемы

### data-api не поддерживает фильтрацию
`https://data-api.polymarket.com/trades` игнорирует параметры `market`, `conditionId`, `startTs` — всегда возвращает последние 500 глобальных трейдов. Решение: 15-секундный цикл чтобы не пропускать трейды.

### Proxy wallets
Все трейдеры на Polymarket используют proxy wallets созданные автоматически — они всегда "новые". WalletHistoryScore почти всегда срабатывает, что снижает его информативность.

### RAM
3.1GB из 3.8GB используется. Основные потребители: k3s (900MB), Prometheus (415MB), Strimzi (240MB), Grafana (165MB). Swap 2GB почти полный. Критично не становится но за этим надо следить.
