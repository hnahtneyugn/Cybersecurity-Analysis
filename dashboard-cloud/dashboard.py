from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google.cloud import bigquery
from fastapi.responses import FileResponse
import os
import uvicorn
from typing import List, Dict, Any


BIGQUERY_TABLE_ID = "big_data_uet_dataset.classified_urls"


app = FastAPI()
client = bigquery.Client()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"],
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, data: Dict[str, Any]):
        for connection in self.active_connections:
            await connection.send_json(data)

manager = ConnectionManager()



@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)



@app.get("/")
def read_root():
    return FileResponse('index.html')


QUERY_GET_STATS = f"""
    SELECT 
        COUNT(*) as total_urls,
        SUM(CASE WHEN classification = 'MALICIOUS' THEN 1 ELSE 0 END) as total_malicious,
        AVG(CASE WHEN classification = 'MALICIOUS' THEN score END) as avg_malicious_score,
        AVG(CASE WHEN classification = 'BENIGN' THEN score END) as avg_benign_score,
        SUM(CASE WHEN classification = 'MALICIOUS' AND score < 0.7 THEN 1 ELSE 0 END) as low_confidence_malicious,
        SUM(CASE WHEN classification = 'BENIGN' AND score > 0.3 THEN 1 ELSE 0 END) as low_confidence_benign
    FROM `{BIGQUERY_TABLE_ID}`
"""

@app.get("/api/stats")
def get_stats():
    try:
        rows = client.query(QUERY_GET_STATS).result()
        row = next(rows)
        return {
            "total_urls": row.total_urls,
            "total_malicious": row.total_malicious,
            "total_benign": row.total_urls - row.total_malicious,
            "malicious_rate": round(row.total_malicious / row.total_urls * 100, 2) if row.total_urls > 0 else 0,
            "benign_rate": round((row.total_urls - row.total_malicious) / row.total_urls * 100, 2) if row.total_urls > 0 else 0,
            "avg_malicious_confidence": round(float(row.avg_malicious_score or 0), 3),
            "avg_benign_confidence": round(float(row.avg_benign_score or 0), 3),
            "low_confidence_malicious": row.low_confidence_malicious,
            "low_confidence_benign": row.low_confidence_benign,
            "false_positive_rate": round(row.low_confidence_benign / row.total_urls * 100, 2) if row.total_urls > 0 else 0
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



QUERY_GET_REALTIME_METRICS = f"""
    SELECT 
        COUNT(*) as total_last_hour,
        SUM(CASE WHEN classification = 'MALICIOUS' THEN 1 ELSE 0 END) as malicious_last_hour,
        MAX(time_added) as latest_detection
    FROM `{BIGQUERY_TABLE_ID}`
    WHERE time_added >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)
"""

@app.get("/api/realtime-metrics")
def get_realtime_metrics():
    try:
        rows = client.query(QUERY_GET_REALTIME_METRICS).result()
        row = next(rows)
        return {
            "total_last_hour": row.total_last_hour,
            "malicious_last_hour": row.malicious_last_hour,
            "latest_detection": row.latest_detection.isoformat() if row.latest_detection else None
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


QUERY_GET_CONFIDENCE_DIST = f"""
    SELECT 
        classification,
        CASE 
            WHEN score >= 0.9 THEN '0.9-1.0'
            WHEN score >= 0.7 THEN '0.7-0.9'
            WHEN score >= 0.5 THEN '0.5-0.7'
            WHEN score >= 0.3 THEN '0.3-0.5'
            WHEN score >= 0.1 THEN '0.1-0.3'
            ELSE '0.0-0.1'
        END as score_range,
        COUNT(*) as count
    FROM `{BIGQUERY_TABLE_ID}`
    GROUP BY classification, score_range
    ORDER BY classification, score_range DESC
"""

@app.get("/api/confidence-distribution")
def get_confidence_distribution():
    try:
        rows = client.query(QUERY_GET_CONFIDENCE_DIST).result()
        data = [{
            "classification": 1 if row.classification == "MALICIOUS" else 0,
            "confidence_range": row.score_range,
            "count": row.count
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


QUERY_GET_TIMELINE = f"""
    SELECT 
        TIMESTAMP_TRUNC(time_added, MINUTE) as time_bucket,
        classification,
        COUNT(*) as count
    FROM `{BIGQUERY_TABLE_ID}`
    WHERE time_added >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 MINUTE)
    GROUP BY time_bucket, classification
    ORDER BY time_bucket ASC
"""

@app.get("/api/timeline")
def get_timeline():
    try:
        rows = client.query(QUERY_GET_TIMELINE).result()
        data = [{
            "time_bucket": row.time_bucket.isoformat() if row.time_bucket else None,
            "classification": 1 if row.classification == "MALICIOUS" else 0,
            "count": row.count
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


QUERY_GET_DETECTION_RATE = f"""
    SELECT 
        DATE(time_added) as date,
        ROUND(SUM(CASE WHEN classification = 'MALICIOUS' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as detection_rate,
        COUNT(*) as total_urls
    FROM `{BIGQUERY_TABLE_ID}`
    WHERE time_added >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
    GROUP BY date
    ORDER BY date ASC
"""

@app.get("/api/detection-rate")
def get_detection_rate():
    try:
        rows = client.query(QUERY_GET_DETECTION_RATE).result()
        data = [{
            "date": row.date.isoformat() if row.date else None,
            "detection_rate": float(row.detection_rate),
            "total_urls": row.total_urls
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



QUERY_GET_TOP_DOMAINS = f"""
    WITH domain_extracted AS (
        SELECT 
            REGEXP_EXTRACT(url, r'(?:https?://)?(?:www\.)?([^/]+)') as domain,
            classification,
            score
        FROM `{BIGQUERY_TABLE_ID}`
        WHERE classification = 'MALICIOUS'
    )
    SELECT 
        domain,
        COUNT(*) as count,
        AVG(score) as avg_score
    FROM domain_extracted
    WHERE domain IS NOT NULL
    GROUP BY domain
    ORDER BY count DESC
    LIMIT 15
"""

@app.get("/api/top-domains")
def get_top_domains():
    try:
        rows = client.query(QUERY_GET_TOP_DOMAINS).result()
        data = [{
            "domain": row.domain,
            "count": row.count,
            "avg_confidence": float(row.avg_score)
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



QUERY_GET_URL_LENGTH = f"""
    SELECT 
        CASE 
            WHEN LENGTH(url) < 30 THEN '0-30'
            WHEN LENGTH(url) < 50 THEN '30-50'
            WHEN LENGTH(url) < 75 THEN '50-75'
            WHEN LENGTH(url) < 100 THEN '75-100'
            ELSE '100+'
        END as length_range,
        classification,
        COUNT(*) as count
    FROM `{BIGQUERY_TABLE_ID}`
    GROUP BY length_range, classification
    ORDER BY length_range
"""

@app.get("/api/url-length")
def get_url_length():
    try:
        rows = client.query(QUERY_GET_URL_LENGTH).result()
        data = [{
            "length_range": row.length_range,
            "is_phishing": 1 if row.classification == "MALICIOUS" else 0,
            "count": row.count
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    


QUERY_GET_RECENT_100 = f"""
    SELECT time_added, url, classification, score
    FROM `{BIGQUERY_TABLE_ID}`
    ORDER BY time_added DESC
    LIMIT 100
"""

@app.get("/api/data")
def get_recent_data():
    try:
        rows = client.query(QUERY_GET_RECENT_100).result()
        data = [{
            "timestamp": row.time_added.isoformat() if row.time_added else None,
            "url": row.url,
            "classification": row.classification,
            "score": float(row.score) if row.score else 0.0
        } for row in rows]
        return list(reversed(data))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



QUERY_GET_TOP_MALICIOUS = f"""
    SELECT url, score, time_added
    FROM `{BIGQUERY_TABLE_ID}`
    WHERE classification = 'MALICIOUS'
    ORDER BY score DESC, time_added DESC
    LIMIT 20
"""

@app.get("/api/top-malicious")
def get_top_malicious():
    try:
        rows = client.query(QUERY_GET_TOP_MALICIOUS).result()
        data = [{
            "url": row.url,
            "confidence": float(row.score),
            "timestamp": row.time_added.isoformat() if row.time_added else None
        } for row in rows]
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/submit_url")
async def submit_url(data: List[Dict[str, Any]]):
    try:
        errors = client.insert_rows_json(BIGQUERY_TABLE_ID, data)
        if errors:
            print(f"Error inserting into BigQuery: {errors}")
            raise HTTPException(status_code=500, detail="Error writing to BigQuery")
        
        await manager.broadcast(data)
        return {"status": "success", "data": data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)