#!/usr/bin/env python3
"""
Hiển thị đồng thời 4 luồng video RTSP từ camera (ví dụ: Hikvision PTZ)
Sử dụng OpenCV + multi-threading để mượt mà hơn.

Yêu cầu:
    pip install opencv-python numpy

Cách dùng:
    1. Thay thế 4 RTSP URL bên dưới bằng URL camera thực tế của bạn.
       Ví dụ Hikvision: rtsp://admin:YourPass@192.168.1.XX:554/Streaming/Channels/101
    2. Chạy: python hien_thi_4_camera_rtsp.py
    3. Nhấn 'q' để thoát, 's' để chụp ảnh màn hình.

Tối ưu cho Jetson Orin:
    - Nếu OpenCV được build với GStreamer, bạn có thể đổi sang backend GStreamer để giảm latency và dùng hardware decode.
    - Xem comment bên dưới để bật GStreamer pipeline.
"""

import cv2
import numpy as np
import threading
import time
import os

# ================== CẤU HÌNH ==================
# Thay thế bằng RTSP URL thực tế của 4 camera
RTSP_URLS = [
    "rtsp://admin:password@192.168.1.101:554/Streaming/Channels/101",  # Camera 1
    "rtsp://admin:password@192.168.1.102:554/Streaming/Channels/101",  # Camera 2
    "rtsp://admin:password@192.168.1.103:554/Streaming/Channels/101",  # Camera 3
    "rtsp://admin:password@192.168.1.104:554/Streaming/Channels/101",  # Camera 4
]

# Kích thước mỗi ô trong lưới 2x2 (có thể chỉnh 640x480, 800x600, v.v.)
CELL_WIDTH = 640
CELL_HEIGHT = 480

# Bật GStreamer backend (khuyến nghị trên Jetson Orin nếu đã cài đúng)
# Đặt True nếu bạn muốn dùng pipeline GStreamer tối ưu (cần OpenCV build với GStreamer + Jetson có nvidia plugins)
USE_GSTREAMER = False

# Tên cửa sổ
WINDOW_NAME = "4 Camera RTSP - Traffic Monitoring"

# =============================================

class RTSPStream:
    """Lớp đọc luồng RTSP trong thread riêng để không block main thread"""
    def __init__(self, url, cam_id):
        self.url = url
        self.cam_id = cam_id
        self.frame = None
        self.ret = False
        self.stopped = False
        self.lock = threading.Lock()
        
        # Khởi tạo capture
        if USE_GSTREAMER:
            # Pipeline GStreamer tối ưu cho Jetson (hardware decode H264/H265)
            # Bạn có thể chỉnh latency=0..200 tùy ý
            gst_pipeline = (
                f"rtspsrc location={url} latency=100 ! "
                "rtph264depay ! h264parse ! nvv4l2decoder ! "
                "nvvidconv ! video/x-raw,format=BGRx ! "
                "videoconvert ! video/x-raw,format=BGR ! "
                "appsink drop=true sync=false max-buffers=1"
            )
            self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
        else:
            self.cap = cv2.VideoCapture(url)
            if self.cap.isOpened():
                self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Giảm buffer để thấp latency
                # Một số camera cần set thêm:
                # self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('H', '2', '6', '4'))
        
        if not self.cap.isOpened():
            print(f"[Camera {cam_id}] ⚠️  Không thể kết nối RTSP: {url}")
        
        # Thread đọc frame
        self.thread = threading.Thread(target=self._update, daemon=True)
        self.thread.start()
    
    def _update(self):
        """Thread liên tục đọc frame mới nhất"""
        while not self.stopped:
            if self.cap.isOpened():
                ret, frame = self.cap.read()
                with self.lock:
                    self.ret = ret
                    if ret:
                        self.frame = frame
            else:
                # Thử reconnect sau 2 giây
                time.sleep(2)
                if not self.stopped:
                    print(f"[Camera {self.cam_id}] Đang thử kết nối lại...")
                    if USE_GSTREAMER:
                        gst_pipeline = (
                            f"rtspsrc location={self.url} latency=100 ! "
                            "rtph264depay ! h264parse ! nvv4l2decoder ! "
                            "nvvidconv ! video/x-raw,format=BGRx ! "
                            "videoconvert ! video/x-raw,format=BGR ! "
                            "appsink drop=true sync=false max-buffers=1"
                        )
                        self.cap = cv2.VideoCapture(gst_pipeline, cv2.CAP_GSTREAMER)
                    else:
                        self.cap = cv2.VideoCapture(self.url)
            time.sleep(0.01)  # Nhường CPU một chút
    
    def read(self):
        """Lấy frame mới nhất (thread-safe)"""
        with self.lock:
            if self.ret and self.frame is not None:
                return True, self.frame.copy()
            return False, None
    
    def stop(self):
        self.stopped = True
        if self.thread.is_alive():
            self.thread.join(timeout=1)
        if self.cap.isOpened():
            self.cap.release()

def resize_with_letterbox(frame, target_w, target_h):
    """Resize frame giữ nguyên tỷ lệ, thêm viền đen nếu cần (letterbox)"""
    if frame is None:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)
    
    h, w = frame.shape[:2]
    scale = min(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    
    resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    
    # Tạo canvas đen
    canvas = np.zeros((target_h, target_w, 3), dtype=np.uint8)
    y_off = (target_h - new_h) // 2
    x_off = (target_w - new_w) // 2
    canvas[y_off:y_off+new_h, x_off:x_off+new_w] = resized
    return canvas

def main():
    print("🚀 Khởi động hiển thị 4 camera RTSP...")
    print(f"   Số camera: {len(RTSP_URLS)}")
    print(f"   Kích thước mỗi ô: {CELL_WIDTH}x{CELL_HEIGHT}")
    print(f"   GStreamer: {'BẬT (tối ưu Jetson)' if USE_GSTREAMER else 'TẮT (dùng backend mặc định)'}")
    print("   Nhấn 'q' để thoát | 's' để chụp ảnh")
    
    # Khởi tạo 4 stream
    streams = []
    for i, url in enumerate(RTSP_URLS):
        stream = RTSPStream(url, i + 1)
        streams.append(stream)
        time.sleep(0.2)  # Tránh mở đồng thời quá nhanh
    
    # Tạo cửa sổ
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, CELL_WIDTH * 2, CELL_HEIGHT * 2)
    
    fps_counter = 0
    fps_start = time.time()
    fps = 0.0
    
    try:
        while True:
            frames = []
            for i, stream in enumerate(streams):
                ret, frame = stream.read()
                
                if not ret or frame is None:
                    # Frame lỗi / chưa có
                    frame = np.zeros((CELL_HEIGHT, CELL_WIDTH, 3), dtype=np.uint8)
                    cv2.putText(frame, f"CAM {i+1}", (20, 40), 
                               cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2)
                    cv2.putText(frame, "Mat ket noi / Dang ket noi...", (20, CELL_HEIGHT//2), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
                else:
                    # Resize + letterbox
                    frame = resize_with_letterbox(frame, CELL_WIDTH, CELL_HEIGHT)
                    
                    # Vẽ nhãn
                    label = f"CAM {i+1}"
                    cv2.putText(frame, label, (15, 35), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
                    
                    # Viền xanh mỏng
                    cv2.rectangle(frame, (0, 0), (CELL_WIDTH-1, CELL_HEIGHT-1), (0, 255, 0), 2)
                
                frames.append(frame)
            
            # Ghép thành lưới 2x2
            top = np.hstack((frames[0], frames[1]))
            bottom = np.hstack((frames[2], frames[3]))
            grid = np.vstack((top, bottom))
            
            # Hiển thị FPS tổng thể
            fps_counter += 1
            if time.time() - fps_start >= 1.0:
                fps = fps_counter
                fps_counter = 0
                fps_start = time.time()
            
            cv2.putText(grid, f"FPS: {fps}", (CELL_WIDTH*2 - 120, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)
            cv2.putText(grid, "Press 'q' to quit | 's' snapshot", (10, CELL_HEIGHT*2 - 15), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)
            
            cv2.imshow(WINDOW_NAME, grid)
            
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                print("\n🛑 Đang thoát...")
                break
            elif key == ord('s'):
                # Chụp ảnh
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                filename = f"snapshot_4cam_{timestamp}.jpg"
                cv2.imwrite(filename, grid)
                print(f"📸 Đã lưu: {filename}")
    
    except KeyboardInterrupt:
        print("\n🛑 Dừng bởi người dùng (Ctrl+C)")
    
    finally:
        # Dọn dẹp
        for stream in streams:
            stream.stop()
        cv2.destroyAllWindows()
        print("✅ Đã giải phóng tài nguyên.")

if __name__ == "__main__":
    main()
