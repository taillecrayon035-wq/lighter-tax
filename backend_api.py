"""
Backend API Flask pour le service de rapport fiscal Lighter
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
CORS(app)  # Permet les requêtes depuis le frontend


@app.route('/')
def index():
    """Page d'accueil - sert le frontend HTML"""
    return send_file('frontend.html')

# Stockage temporaire des résultats (en production, utiliser Redis)
reports = {}

def generate_lighter_report(token, account_index, report_id):
    """
    Fonction qui génère le rapport fiscal
    (C'est ton script adapté)
    """
    try:
        # Mettre à jour le status
        reports[report_id]['status'] = 'running'
        reports[report_id]['progress'] = 0
        
        # ===== TON SCRIPT ICI =====
        all_logs = []
        page = 1
        last_time = None
        last_batch_time = None
        
        while True:
            reports[report_id]['progress'] = min(page * 2, 90)  # Max 90% pendant la collecte
            reports[report_id]['current_page'] = page
            
            url = f"https://explorer.elliot.ai/api/accounts/{account_index}/logs"
            
            if last_time:
                url += f"?before={last_time}"
            
            try:
                response = requests.get(url, timeout=10)
                
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
        all_logs_2025 = [log for log in all_logs if log.get('time', '').startswith('2025-')]
        
        # Classification (version simplifiée pour l'exemple)
        trades = []
        deposits = []
        withdrawals = []
        transfers = []
        
        seen_trades = set()
        
        for log in all_logs_2025:
            tx_type = log.get('tx_type', '')
            status = log.get('status', '')
            pubdata = log.get('pubdata', {})
            
            if 'InternalClaimOrder' in tx_type and status == 'executed':
                trade_data = pubdata.get('trade_pubdata', {})
                if trade_data:
                    trade_key = (
                        log['time'],
                        trade_data.get('market_index'),
                        trade_data.get('size'),
                        trade_data.get('price')
                    )
                    
                    if trade_key not in seen_trades:
                        seen_trades.add(trade_key)
                        trades.append({
                            'time': log['time'],
                            'market_id': trade_data.get('market_index'),
                            'size': float(trade_data.get('size', 0)),
                            'price': float(trade_data.get('price', 0)),
                            'is_sell': trade_data.get('is_taker_ask') == 1,
                            'maker_fee': trade_data.get('maker_fee', 0),
                            'taker_fee': trade_data.get('taker_fee', 0)
                        })
        
        # Calcul rapide du volume et fees
        total_volume = sum(t['size'] * t['price'] for t in trades)
        total_fees = sum(t['size'] * t['price'] * (t['maker_fee'] + t['taker_fee']) / 10000 for t in trades)
        
        # Résultat final
        result = {
            'summary': {
                'account_index': account_index,
                'year': 2025,
                'total_trades': len(trades),
                'total_volume': total_volume,
                'total_fees': total_fees,
                'period_start': all_logs_2025[-1]['time'] if all_logs_2025 else None,
                'period_end': all_logs_2025[0]['time'] if all_logs_2025 else None
            },
            'trades': trades,
            'deposits': deposits,
            'withdrawals': withdrawals,
            'transfers': transfers
        }
        
        # Sauvegarder les fichiers
        output_dir = f"reports/{report_id}"
        os.makedirs(output_dir, exist_ok=True)
        
        # JSON
        with open(f"{output_dir}/fiscal_report.json", 'w') as f:
            json.dump(result, f, indent=2)
        
        # CSV trades
        import csv
        with open(f"{output_dir}/trades.csv", 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Date', 'Time', 'Market', 'Type', 'Size', 'Price', 'USD Amount', 'Fee USD'])
            for trade in trades:
                dt = datetime.fromisoformat(trade['time'].replace('Z', '+00:00'))
                usd_amount = trade['size'] * trade['price']
                fee_usd = usd_amount * (trade['maker_fee'] + trade['taker_fee']) / 10000
                writer.writerow([
                    dt.strftime('%Y-%m-%d'),
                    dt.strftime('%H:%M:%S'),
                    trade['market_id'],
                    'SELL' if trade['is_sell'] else 'BUY',
                    trade['size'],
                    trade['price'],
                    usd_amount,
                    fee_usd
                ])
        
        # Mettre à jour le status
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


@app.route('/api/generate-report', methods=['POST'])
def generate_report():
    """
    Endpoint principal pour générer un rapport
    
    Body JSON:
    {
        "token": "ro:524876:single:...",
        "account_index": 524876  // optionnel
    }
    """
    data = request.json
    token = data.get('token')
    
    if not token:
        return jsonify({'error': 'Token manquant'}), 400
    
    # Extraire l'account_index du token si pas fourni
    account_index = data.get('account_index')
    if not account_index:
        try:
            # Format: ro:ACCOUNT_INDEX:...
            account_index = int(token.split(':')[1])
        except:
            return jsonify({'error': 'Account index invalide'}), 400
    
    # Générer un ID unique pour ce rapport
    report_id = str(uuid.uuid4())
    
    # Initialiser le rapport
    reports[report_id] = {
        'status': 'pending',
        'progress': 0,
        'created_at': datetime.now().isoformat()
    }
    
    # Lancer la génération en arrière-plan
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
    """
    Vérifier le status d'un rapport
    """
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
    """
    Télécharger un fichier (json ou csv)
    """
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
    """Health check"""
    return jsonify({'status': 'ok'})


if __name__ == '__main__':
    # Créer le dossier reports
    os.makedirs('reports', exist_ok=True)
    
    # Port pour Railway (utilise la variable d'environnement PORT)
    port = int(os.environ.get('PORT', 5000))
    
    # Lancer le serveur
    print(f"🚀 Serveur démarré sur le port {port}")
    app.run(debug=False, host='0.0.0.0', port=port)
