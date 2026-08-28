# ==============================================================================
# GESTORE FORMATO .SER (Standard Astronomico Luc Coiffier v3)
# ==============================================================================
import struct
import datetime
import numpy as np

# Tabella ColorID standard SER
COLOR_MONO = 0
COLOR_BAYER_RGGB = 8
COLOR_BAYER_BGGR = 9
COLOR_BAYER_GRBG = 10
COLOR_BAYER_GBRG = 11
COLOR_RGB = 100
COLOR_BGR = 101

def get_ser_timestamp(dt=None):
    """Calcola il timestamp nel formato a 64 bit di Windows/SER (100ns dal 01/01/0001)."""
    if dt is None:
        dt = datetime.datetime.now(datetime.timezone.utc)
    epoch = datetime.datetime(1, 1, 1, tzinfo=datetime.timezone.utc)
    delta = dt - epoch
    return int(delta.total_seconds() * 10_000_000)

def write_ser(filename, frames, width, height, color_id=COLOR_MONO, pixel_depth=8, observer="Fulmini", instrument="ASI Camera"):
    """
    Scrive una sequenza di frame (numpy arrays) in un file .SER standard v3.
    """
    frame_count = len(frames)
    if frame_count == 0:
        return

    # Header da 178 byte
    header = bytearray(178)
    # FileID (14 byte)
    header[0:14] = b"LUCAM-RECORDER"
    # LuID (4 byte) = 0
    struct.pack_into("<i", header, 14, 0)
    # ColorID (4 byte)
    struct.pack_into("<i", header, 18, int(color_id))
    # LittleEndian (4 byte) = 0 (Little Endian x86)
    struct.pack_into("<i", header, 22, 0)
    # ImageWidth & ImageHeight (4 byte ciascuno)
    struct.pack_into("<i", header, 26, int(width))
    struct.pack_into("<i", header, 30, int(height))
    # PixelDepthPerPlane (4 byte) (es. 8 o 16)
    struct.pack_into("<i", header, 34, int(pixel_depth))
    # FrameCount (4 byte)
    struct.pack_into("<i", header, 38, int(frame_count))
    # Observer (40 byte)
    obs_bytes = observer.encode("ascii", errors="ignore")[:40]
    header[42 : 42 + len(obs_bytes)] = obs_bytes
    # Instrument (40 byte)
    inst_bytes = instrument.encode("ascii", errors="ignore")[:40]
    header[82 : 82 + len(inst_bytes)] = inst_bytes
    # Telescope (40 byte)
    tele_bytes = b"Wide Angle Lens"[:40]
    header[122 : 122 + len(tele_bytes)] = tele_bytes
    
    # DateTime (8 byte) & DateTime_UTC (8 byte)
    ts = get_ser_timestamp()
    struct.pack_into("<q", header, 162, ts)
    struct.pack_into("<q", header, 170, ts)

    with open(filename, "wb") as f:
        f.write(header)
        for frame in frames:
            f.write(frame.tobytes())

def read_ser(filename):
    """
    Legge un file .SER e restituisce i metadati e la lista dei frame come numpy arrays.
    """
    with open(filename, "rb") as f:
        header = f.read(178)
        if len(header) < 178 or header[0:14] != b"LUCAM-RECORDER":
            raise ValueError(f"Il file {filename} non e' un file .SER valido.")

        color_id = struct.unpack_from("<i", header, 18)[0]
        little_endian = struct.unpack_from("<i", header, 22)[0]
        width = struct.unpack_from("<i", header, 26)[0]
        height = struct.unpack_from("<i", header, 30)[0]
        pixel_depth = struct.unpack_from("<i", header, 34)[0]
        frame_count = struct.unpack_from("<i", header, 38)[0]

        is_16bit = pixel_depth > 8
        dtype = np.uint16 if is_16bit else np.uint8
        bytes_per_pixel = 2 if is_16bit else 1

        is_color = color_id in (COLOR_RGB, COLOR_BGR)
        channels = 3 if is_color else 1
        frame_size_bytes = width * height * bytes_per_pixel * channels

        frames = []
        for _ in range(frame_count):
            buf = f.read(frame_size_bytes)
            if len(buf) < frame_size_bytes:
                break
            if channels == 3:
                arr = np.frombuffer(buf, dtype=dtype).reshape((height, width, 3))
            else:
                arr = np.frombuffer(buf, dtype=dtype).reshape((height, width))
            frames.append(arr)

    metadata = {
        "width": width,
        "height": height,
        "color_id": color_id,
        "pixel_depth": pixel_depth,
        "frame_count": len(frames),
        "is_color": is_color
    }
    return metadata, frames
