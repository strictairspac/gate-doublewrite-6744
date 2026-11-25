import hashlib
from dataclasses import dataclass, replace


def digest(key, generation, data):
    # Include the logical page address and generation so images cannot be silently swapped.
    prefix = f"{key}:{generation}:{len(data)}:".encode()
    return hashlib.sha256(prefix + data).hexdigest()


@dataclass(frozen=True)
class Image:
    key: str
    generation: int
    data: bytes
    checksum: str

    @classmethod
    def seal(cls, key, generation, text):
        data = text.encode()
        return cls(key, generation, data, digest(key, generation, data))

    def valid(self, key):
        return self.key == key and self.checksum == digest(self.key, self.generation, self.data)

    def torn(self, prior, prefix_bytes):
        # Persist a short prefix of the intended image and keep the prior tail.
        return replace(self, data=self.data[:prefix_bytes] + prior.data[prefix_bytes:])

    def damaged(self):
        return replace(self, data=bytes([self.data[0] ^ 1]) + self.data[1:])
