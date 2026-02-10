import socket

def check_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except:
            return False

print(f"Checking localhost:3306: {check_port('127.0.0.1', 3306)}")
print(f"Checking 192.168.64.2:3306: {check_port('192.168.64.2', 3306)}")
