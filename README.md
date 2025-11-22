# Cybersecurity-Analysis

README này mô tả dự án **“Hệ thống phân loại và đánh giá URL độc hại theo thời gian thực sử dụng Streaming và Machine Learning”**, được triển khai trên Google Cloud Platform với mô hình Deep Learning 1D-CNN mức ký tự.

---

## 1. Mục tiêu

- Phân loại URL thành:
  - `BENIGN` – URL lành tính  
  - `MALICIOUS` – URL độc hại
- Cung cấp **điểm tin cậy (confidence score ∈ [0,1])** để đánh giá mức độ nguy hiểm.
- Xử lý **thời gian thực (real-time)** với độ trễ trung bình < 500ms và throughput tối thiểu ~200 URL/s.
- Hỗ trợ **song song**:
  - **Batch pipeline**: huấn luyện mô hình trên dữ liệu lịch sử (~7M URLs, cân bằng 50/50).
  - **Streaming pipeline**: phân loại URL mới lấy từ Certstream server.

---

## 2. Tính năng chính

- 🧠 **Mô hình Deep Learning 1D-CNN mức ký tự**
  - Sử dụng `TextVectorization` + `Embedding` + nhiều nhánh `Conv1D` (kernel 3/5/7).  
  - Đầu vào là chuỗi URL *đã chuẩn hóa*, không cần trích xuất feature thủ công.
  - Kết quả trên tập test:
    - Accuracy: **98.26%**
    - Precision: **99.17%**
    - Recall: **97.34%**
    - F1-Score: **98.25%**
    - AUC-ROC: **0.9958**
    - PR-AUC: **0.9969** 

- ⚡ **Streaming real-time trên Google Cloud**
  - Certstream server (Go) → GCE Publisher → Pub/Sub → Cloud Function → BigQuery.
  - Dự đoán theo **lô** (batch) và ghi kết quả vào BigQuery.
  - Dashboard giám sát real-time với **Looker Studio** + **Highcharts**.

- ☁️ **Kiến trúc serverless & IaC**
  - Pub/Sub & Cloud Function giúp tự động scale.
  - Hạ tầng được quản lý bằng **Terraform** (Infrastructure as Code).

---

## 3. Kiến trúc tổng quan

Kiến trúc chi tiết được minh họa trong **sơ đồ Hình 1 (trang 7)** của báo cáo. Dưới đây là tóm tắt các thành phần chính: 

![Alt text](diagram_workflow.png "Tiêu đề (workflow)")

- **Lightning AI / Training environment**
  - Huấn luyện mô hình 1D-CNN trên dữ liệu batch.
- **Google Cloud Storage (GCS)**
  - Lưu:
    - Dữ liệu huấn luyện (benign/malicious).
    - File mô hình `url_classifier_model.keras` / `best_model.keras`.
    - Gói ZIP chứa code inference + model để Cloud Function tải.
- **Certstream Server (Go)**
  - Tự host, stream các bản ghi Certificate Transparency qua WebSocket.
- **Google Compute Engine (GCE) – Publisher**
  - Script `producer.py`:
    - Kết nối WebSocket tới Certstream Server.
    - Gom các domain trong message `"certificate_update"`.
    - Đóng gói JSON `{"urls": [...]}` rồi publish lên Pub/Sub topic `urlstream`.
- **Google Cloud Pub/Sub – Message Queue**
  - Là backbone truyền message giữa Publisher và Subscriber (Cloud Function).
- **Google Cloud Function – Subscriber / Orchestrator**
  - Hàm `process_pubsub` trong `evaluating_url.py`:
    - Decode message Pub/Sub.
    - Lọc URL wildcard (`*.domain`).
    - Tiền xử lý URL, gọi mô hình dự đoán theo batch.
    - Ghi kết quả vào BigQuery.
- **Google BigQuery – Data Warehouse**
  - Lưu:
    - URL gốc
    - Điểm dự đoán (`score`)
    - Nhãn (`MALICIOUS` / `BENIGN`)
    - `timestamp` và các metadata khác.
- **Looker Studio & Highcharts – Visualization**
  - Dashboard real-time giám sát số lượng URL độc hại, xu hướng theo thời gian…

---

## 4. Mô hình Machine Learning

### 4.1 Dữ liệu & tiền xử lý

- Dữ liệu batch:
  - `benign.txt` – URL lành tính  
  - `malicious.txt` – URL độc hại  
  - Tổng sau khi làm sạch: ~7,000,000 URL, cân bằng 50/50. 
- Các bước tiền xử lý:
  1. `strip()` khoảng trắng + ký tự thừa; chuyển toàn bộ về **chữ thường**.
  2. Loại bỏ tiền tố: `http://`, `https://`, `www.` để đồng bộ với dữ liệu streaming.
  3. Giới hạn độ dài URL; dùng `output_sequence_length = 200` cho `TextVectorization`.
  4. Tokenization ở **mức ký tự** (char-level).
- Vector hóa:
  - `TextVectorization(max_tokens=128, output_sequence_length=200)`
  - `Embedding(input_dim=128, output_dim=64)`

### 4.2 Kiến trúc 1D-CNN

- Khối nhúng:
  - `Embedding(128, 64)`
- Ba nhánh tích chập song song:
  - `Conv1D(128, 3)` + `BatchNormalization`
  - `Conv1D(128, 5)` + `BatchNormalization`
  - `Conv1D(128, 7)` + `BatchNormalization`
- Hợp nhất & pooling:
  - `Concatenate` → `GlobalMaxPooling1D`
- Head phân loại:
  - `Dense(128, activation="relu")` → `BatchNormalization` → `Dropout`
  - `Dense(1, activation="sigmoid")`

### 4.3 Huấn luyện

- Optimizer: `Adam(lr=1e-3)`
- Loss: `binary_crossentropy`
- Metrics: `accuracy`, `precision`, `recall`
- Batch size: `512`, Epochs: tối đa 20 (EarlyStopping)
- Callbacks:
  - `EarlyStopping(patience=3, restore_best_weights=True)`
  - `ReduceLROnPlateau(factor=0.5, patience=2, min_lr=1e-7)`
  - `ModelCheckpoint` (lưu `best_model.keras`)

Kết quả đường cong học (Hình 6, trang 22) cho thấy train/val loss hội tụ tốt, không overfitting.

![Alt text](src_model/results/training_history.png "Tiêu đề (training_history)")


---

## 5. Yêu cầu hệ thống

### 5.1 Môi trường phát triển

- Python 3.9+  
- `pip` / `venv` hoặc `conda`
- TensorFlow / Keras
- Thư viện Google Cloud:
  - `google-cloud-pubsub`
  - `google-cloud-bigquery`
  - `google-cloud-storage`
- Lightning AI (tuỳ chọn, cho môi trường training)

### 5.2 Hạ tầng Google Cloud

- 1 Google Cloud Project (đã bật billing).
- Dịch vụ:
  - Compute Engine (GCE)
  - Cloud Pub/Sub
  - Cloud Functions
  - Cloud Storage
  - BigQuery
- Terraform / gcloud CLI để triển khai IaC.
- Docker & Go để chạy Certstream server (Go).

## Kết quả dự án được trình bày ở : [Dashboard Dự án] (https://dashboard-cloud-938296883293.us-central1.run.app/)

## Giới thiệu thành viên 

- **Đào Tự Phát** – MSSV: `23020409`  
- **Hoàng Minh Quyền** – MSSV: `23020421`  
- **Tạ Nguyên Thành** – MSSV: `23020437`  

