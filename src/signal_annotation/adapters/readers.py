"""Local input adapters. ACQ bytes never leave this process."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import numpy as np
from ..domain import Channel, Recording, ValidationError

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHANNELS = 8


class BioreadReader:
    def read(self, path: Path) -> Recording:
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != ".acq" or not path.is_file():
            raise ValidationError("Select an existing local .acq file.")
        if not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            raise ValidationError("The file must be between 1 byte and 20 MiB.")
        import bioread  # Infrastructure dependency stays outside the domain.
        with path.open("rb") as f:
            content = f.read(MAX_FILE_BYTES + 1)
        if not 0 < len(content) <= MAX_FILE_BYTES:
            raise ValidationError("The file changed or exceeds the 20 MiB limit.")
        identity = sha256(content).hexdigest()
        try:
            data = bioread.read(BytesIO(content))
        except Exception as exc:
            raise ValidationError("bioread could not read the ACQ file. Check its version and integrity.") from exc
        if not 1 <= len(data.channels) <= MAX_CHANNELS:
            raise ValidationError("This release supports 1 to 8 channels.")
        if sum(len(c.data) for c in data.channels) > 12_000_000:
            raise ValidationError("The decoded ACQ file exceeds the 12-million-sample limit.")
        channels = []
        for i, ch in enumerate(data.channels):
            values = np.array(ch.data, dtype=np.float64, copy=True)
            if values.ndim != 1:
                raise ValidationError("Channel data is not one-dimensional.")
            values.setflags(write=False)
            # Sample time = index / channel rate. bioread 3.1.0 time_index uses
            # endpoint-inclusive linspace; do not propagate its timing stretch.
            channels.append(Channel(f"ch{i}", str(ch.name), str(ch.units),
                                    float(ch.samples_per_second), values))
        return Recording(identity, path.name, tuple(channels), source_size_bytes=len(content))


def demo_recording() -> Recording:
    """Deterministic illustrative waveforms. Not physiological ground truth."""
    rng = np.random.default_rng(2026)
    channels = []
    for i, (name, units, rate) in enumerate([
        ("Synthetic ECG I", "mV", 500), ("Synthetic ECG II", "mV", 500),
        ("Synthetic ECG III", "mV", 250), ("Synthetic plethysmography", "a.u.", 125),
        ("Synthetic SpO₂", "%", 25), ("Synthetic pulse", "a.u.", 100),
    ]):
        t = np.arange(int(30 * rate)) / rate
        if i < 3:
            phase = ((t - 0.03 * i) % 0.8) / 0.8
            y = (1.2 * np.exp(-((phase - .30) / .022)**2)
                 - .20 * np.exp(-((phase - .34) / .027)**2)
                 + .18 * np.exp(-((phase - .65) / .11)**2)
                 + .025 * np.sin(2 * np.pi * .2 * t)) * (1 - i * .17)
        elif i == 4:
            y = 97 + .3 * np.sin(2 * np.pi * .05 * t)
        else:
            y = .5 + .4 * np.sin(2 * np.pi * 1.25 * (t - .20)) + .1 * np.sin(2 * np.pi * 2.5 * t)
        y += rng.normal(0, .006, len(t))
        y.setflags(write=False)
        channels.append(Channel(f"ch{i}", name, units, rate, y))
    return Recording("synthetic-demo-v1", "SYNTHETIC DEMO · 30 s · no patient data", tuple(channels), True)
