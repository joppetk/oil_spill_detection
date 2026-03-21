# companion_ops.py
import asyncio

from gpiozero import OutputDevice
from gpiozero.pins.lgpio import LGPIOFactory


class CompanionOps:
    def __init__(self, deploy_pin=18, active_high=True, dry_run=False):
        self.deploy_pin_num = deploy_pin
        self.active_high = active_high
        self.dry_run = dry_run

        self._deploy_pin = None
        self._factory = None
        self._lock = asyncio.Lock()

    async def connect(self):
        if self.dry_run:
            print(f"[CompanionOps] DRY RUN enabled, deploy_pin={self.deploy_pin_num}")
            return

        self._factory = LGPIOFactory()
        self._deploy_pin = OutputDevice(
            self.deploy_pin_num,
            active_high=self.active_high,
            initial_value=False,
            pin_factory=self._factory,
        )
        print(f"[CompanionOps] GPIO ready on pin {self.deploy_pin_num}")

    async def close(self):
        async with self._lock:
            if self._deploy_pin is not None:
                try:
                    self._deploy_pin.off()
                finally:
                    self._deploy_pin.close()
                    self._deploy_pin = None

    async def pulse_deploy(self, duration_s: float = 5.0):
        duration_s = max(0.0, float(duration_s))
        async with self._lock:
            if self.dry_run:
                print(f"[CompanionOps] pulse_deploy({duration_s}s)")
                await asyncio.sleep(duration_s)
                return

            if self._deploy_pin is None:
                raise RuntimeError("CompanionOps not initialized")

            self._deploy_pin.on()
            try:
                await asyncio.sleep(duration_s)
            finally:
                self._deploy_pin.off()

    async def deploy_chemicals(self, duration_s: float = 5.0):
        await self.pulse_deploy(duration_s)
