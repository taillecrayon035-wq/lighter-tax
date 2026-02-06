"""
Backend API Flask pour le service de rapport fiscal Lighter  
Version 3.0 - Using PROVEN FIFO calculation from working script
"""

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import websocket
import json
import time
from collections import defaultdict
from datetime import datetime
import requests
import threading
import uuid
import os

app = Flask(__name__)
CORS(app)


@app.route('/')
def index():
    """Page d'accueil - sert le frontend HTML"""
    return send_file('frontend.html')


# Stockage temporaire des résultats
reports = {}


def generate_lighter_report(token, account_index, report_id):
    """Génère le rapport fiscal - LOGIQUE EXACTE DU SCRIPT QUI MARCHE"""
    try:
        reports[report_id]['status'] = 'running'
        reports[report_id]['progress'] = 0
        
        # ===== RÉCUPÉRATION =====
        all_logs = []
        page = 1
        last_time = None
        last_batch_time = None
        
        while True:
            reports[report_id]['progress'] = min(page * 2, 90)
            reports[report_id]['current_page'] = page
            
            url = f"https://explorer.elliot.ai/api/accounts/{account_index}/logs"
            if last_time:
                url += f"?before={last_time}"
            
            try:
                response = requests.get(url, timeout=30)
                
                if response.status_code == 429:
                    time.sleep(30)
                    continue
                
                if response.status_code != 200:
                    break
                
                logs = response.json()
                
                if not logs or len(logs) == 0:
                    break
                
                current_batch_time = logs[-1]['time']
                if last_batch_time and current_batch_time == last_batch_time:
                    break
                
                all_logs.extend(logs)
                last_time = logs[-1]['time']
                last_batch_time = current_batch_time
                page += 1
                
                time.sleep(0.5)
                
            except Exception as e:
                reports[report_id]['error'] = str(e)
                break
        
        # Déduplication
        seen_tx_keys = set()
        unique_logs = []
        
        for log in all_logs:
            tx_key = (
                log.get('time'),
                log.get('tx_type'),
                str(log.get('pubdata', {}).get('trade_pubdata', {})),
                str(log.get('pubdata', {}).get('l2_transfer_pubdata_v2', {}))
            )
            
            if tx_key not in seen_tx_keys:
                seen_tx_keys.add(tx_key)
                unique_logs.append(log)
        
        all_logs = unique_logs
        
        # Filtre 2025
        all_logs = [log for log in all_logs if log.get('time', '').startswith('2025-')]
        
        # ===== CLASSIFICATION =====
        trades = []
        deposits = []
        withdrawals = []
        transfers = []
        
        seen_trades = set()
        
        for log in all_logs:
            tx_type = log.get('tx_type', '')
            status = log.get('status', '')
            pubdata = log.get('pubdata', {})
            
            if 'InternalClaimOrder' in tx_type or 'TradeWithFunding' in tx_type:
                trade_data = pubdata.get('trade_pubdata', {})
                
                if trade_data and status == 'executed':
                    trade_key = (
                        log['time'],
                        trade_data.get('market_index'),
                        trade_data.get('size'),
                        trade_data.get('price'),
                        trade_data.get('is_taker_ask')
                    )
                    
                    if trade_key not in seen_trades:
                        seen_trades.add(trade_key)
                        trades.append({
                            'timestamp': int(datetime.fromisoformat(log['time'].replace('Z', '+00:00')).timestamp()),
                            'time': log['time'],
                            'market_id': trade_data.get('market_index'),
                            'size': trade_data.get('size'),
                            'price': trade_data.get('price'),
                            'is_taker_ask': trade_data.get('is_taker_ask'),
                            'maker_fee': trade_data.get('maker_fee', 0),
                            'taker_fee': trade_data.get('taker_fee', 0),
                            'funding_rate': pubdata.get('funding_rate_prefix_sum', 0) if 'TradeWithFunding' in tx_type else 0,
                            'tx_hash': log.get('tx_hash', ''),
                            'tx_type': tx_type,
                            'status': status
                        })
            
            elif 'Deposit' in tx_type or 'L1ToL2' in tx_type:
                deposits.append({'time': log['time'], 'tx_type': tx_type})
            
            elif 'Withdraw' in tx_type or 'L2ToL1' in tx_type:
                withdrawals.append({'time': log['time'], 'tx_type': tx_type})
            
            elif 'Transfer' in tx_type:
                transfers.append({'time': log['time'], 'tx_type': tx_type})
        
        # ===== CALCUL PNL - LOGIQUE EXACTE DU SCRIPT QUI MARCHE =====
        
        # Grouper par market
        markets_stats = defaultdict(lambda: {
            'symbol': '',
            'buys': [],
            'sells': [],
            'total_fees': 0,
            'total_volume': 0,
            'trades_count': 0
        })
        
        # Mapper market_id vers symbol
        market_symbols = {
            1: 'BTC',
            24: 'HYPE',
            2048: 'UNKNOWN'
        }
        
        for trade in trades:
            market_id = str(trade.get('market_id'))
            markets_stats[market_id]['symbol'] = market_symbols.get(int(market_id), f'MARKET_{market_id}')
            
            size = float(trade.get('size', 0))
            price = float(trade.get('price', 0))
            is_sell = trade.get('is_taker_ask') == 1
            
            maker_fee = trade.get('maker_fee', 0)
            taker_fee = trade.get('taker_fee', 0)
            usd_amount = size * price
            fee_usd = usd_amount * (maker_fee + taker_fee) / 10000
            
            trade_info = {
                'size': size,
                'price': price,
                'fee': fee_usd,
                'time': trade['time']
            }
            
            if not is_sell:
                markets_stats[market_id]['buys'].append(trade_info)
            else:
                markets_stats[market_id]['sells'].append(trade_info)
            
            markets_stats[market_id]['total_fees'] += fee_usd
            markets_stats[market_id]['total_volume'] += usd_amount
            markets_stats[market_id]['trades_count'] += 1
        
        # Calcul PnL FIFO par market - EXACTEMENT COMME LE SCRIPT QUI MARCHE
        total_pnl = 0
        total_fees = 0
        total_volume = 0
        
        print("\n===== CALCUL PNL (logique du script qui marche) =====")
        
        for market_id in sorted(markets_stats.keys(), key=lambda x: markets_stats[x]['total_volume'], reverse=True):
            stats = markets_stats[market_id]
            symbol = stats['symbol']
            
            # PnL FIFO - COPIE EXACTE
            buys_queue = stats['buys'].copy()
            sells_queue = stats['sells'].copy()
            market_pnl = 0
            
            for sell in sells_queue:
                remaining = sell['size']
                
                while remaining > 0 and len(buys_queue) > 0:
                    buy = buys_queue[0]
                    matched = min(remaining, buy['size'])
                    market_pnl += (sell['price'] - buy['price']) * matched
                    
                    remaining -= matched
                    buys_queue[0]['size'] -= matched
                    
                    if buys_queue[0]['size'] <= 0.0001:
                        buys_queue.pop(0)
            
            total_pnl += market_pnl
            total_fees += stats['total_fees']
            total_volume += stats['total_volume']
            
            print(f"Market {market_id} ({symbol}): PnL = ${market_pnl:.2f}")
        
        pnl_net = total_pnl - total_fees
        
        print(f"PnL total: ${total_pnl:.2f}")
        print(f"Fees: ${total_fees:.2f}")
        print(f"PnL net: ${pnl_net:.2f}")
        
        # Résultat final
        result = {
            'summary': {
                'account_index': account_index,
                'year': 2025,
                'total_trades': len(trades),
                'total_buys': sum(len(s['buys']) for s in markets_stats.values()),
                'total_sells': sum(len(s['sells']) for s in markets_stats.values()),
                'total_volume': round(total_volume, 2),
                'total_fees': round(total_fees, 2),
                'pnl_gross': round(total_pnl, 2),
                'pnl_net': round(pnl_net, 2),
                'period_start': all_logs[-1]['time'] if all_logs else None,
                'period_end': all_logs[0]['time'] if all_logs else None
            },
            'trades': trades,
            'deposits': deposits,
            'withdrawals': withdrawals,
            'transfers': transfers
        }
        
        # Sauvegarder les fichiers
        output_dir = f"reports/{report_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        with open(f"{output_dir}/fiscal_report.json", 'w') as f:
            json.dump(result, f, indent=2)
        
        # CSV trades
        import csv
        with open(f"{output_dir}/trades.csv", 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Time', 'Market', 'Type', 'Size', 'Price', 'USD Amount', 'Fee USD'])
            
            for trade in trades:
                market_id = str(trade.get('market_id'))
                symbol = markets_stats[market_id]['symbol']
                size = float(trade.get('size', 0))
                price = float(trade.get('price', 0))
                is_sell = trade.get('is_taker_ask') == 1
                
                maker_fee = trade.get('maker_fee', 0)
                taker_fee = trade.get('taker_fee', 0)
                usd_amount = size * price
                fee_usd = usd_amount * (maker_fee + taker_fee) / 10000
                
                dt = datetime.fromisoformat(trade['time'].replace('Z', '+00:00'))
                
                writer.writerow([
                    dt.strftime('%Y-%m-%d'),
                    dt.strftime('%H:%M:%S'),
                    market_id,
                    'SELL' if is_sell else 'BUY',
                    size,
                    price,
                    usd_amount,
                    fee_usd
                ])
        
        reports[report_id]['status'] = 'completed'
        reports[report_id]['progress'] = 100
        reports[report_id]['result'] = result
        reports[report_id]['files'] = {
            'json': f"{output_dir}/fiscal_report.json",
            'csv': f"{output_dir}/trades.csv"
        }
        
    except Exception as e:
        reports[report_id]['status'] = 'error'
        reports[report_id]['error'] = str(e)
        import traceback
        print(f"ERROR: {traceback.format_exc()}")


@app.route('/api/generate-report', methods=['POST'])
def generate_report():
    data = request.json
    token = data.get('token')
    
    if not token:
        return jsonify({'error': 'Token manquant'}), 400
    
    account_index = data.get('account_index')
    if not account_index:
        try:
            account_index = int(token.split(':')[1])
        except:
            return jsonify({'error': 'Account index invalide'}), 400
    
    report_id = str(uuid.uuid4())
    
    reports[report_id] = {
        'status': 'pending',
        'progress': 0,
        'created_at': datetime.now().isoformat()
    }
    
    thread = threading.Thread(
        target=generate_lighter_report,
        args=(token, account_index, report_id)
    )
    thread.start()
    
    return jsonify({
        'report_id': report_id,
        'status': 'pending',
        'message': 'Génération du rapport en cours...'
    })


@app.route('/api/report-status/<report_id>', methods=['GET'])
def report_status(report_id):
    if report_id not in reports:
        return jsonify({'error': 'Rapport non trouvé'}), 404
    
    report = reports[report_id]
    
    response = {
        'status': report['status'],
        'progress': report.get('progress', 0),
        'current_page': report.get('current_page', 0)
    }
    
    if report['status'] == 'completed':
        response['summary'] = report['result']['summary']
    
    if report['status'] == 'error':
        response['error'] = report.get('error')
    
    return jsonify(response)


@app.route('/api/download/<report_id>/<file_type>', methods=['GET'])
def download_file(report_id, file_type):
    if report_id not in reports:
        return jsonify({'error': 'Rapport non trouvé'}), 404
    
    report = reports[report_id]
    
    if report['status'] != 'completed':
        return jsonify({'error': 'Rapport pas encore prêt'}), 400
    
    if file_type not in report['files']:
        return jsonify({'error': 'Type de fichier invalide'}), 400
    
    filepath = report['files'][file_type]
    
    return send_file(
        filepath,
        as_attachment=True,
        download_name=f"lighter_fiscal_2025.{file_type}"
    )


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    os.makedirs('reports', exist_ok=True)
    
    port = int(os.environ.get('PORT', 5000))
    
    print(f"🚀 Serveur démarré sur le port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
