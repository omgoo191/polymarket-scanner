from prometheus_client import Counter, Gauge, start_http_server

# Счётчики
cycles_total = Counter(
    'radar_cycles_total',
    'Total number of polling cycles'
)

trades_processed_total = Counter(
    'radar_trades_processed_total',
    'Total number of trades processed'
)

alerts_sent_total = Counter(
    'radar_alerts_sent_total',
    'Total number of alerts sent',
    ['severity']  # STRONG / MEDIUM
)

api_errors_total = Counter(
    'radar_api_errors_total',
    'Total API errors',
    ['source']  # polymarket / polygonscan / telegram
)

# Gauge — текущее значение
markets_monitored = Gauge(
    'radar_markets_monitored',
    'Number of insider-risk markets currently monitored'
)


def start_metrics_server(port: int = 8000):
    start_http_server(port)