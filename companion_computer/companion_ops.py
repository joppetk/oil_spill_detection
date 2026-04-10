# companion_ops.py
import asyncio
from datetime import datetime, timezone

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

        self.deploy_state = "LOW"
        self.last_toggle = None

    def _mark_state(self, state: str):
        self.deploy_state = state
        self.last_toggle = datetime.now(timezone.utc).isoformat()

    def get_gpio_status(self):
        return {
            "pin": f"GPIO{self.deploy_pin_num}",
            "state": self.deploy_state,
            "last_toggle": self.last_toggle,
        }

    async def connect(self):
        if self.dry_run:
            print(f"[CompanionOps] DRY RUN enabled, deploy_pin={self.deploy_pin_num}")
            self._mark_state("LOW")
            return

        self._factory = LGPIOFactory()
        self._deploy_pin = OutputDevice(
            self.deploy_pin_num,
            active_high=self.active_high,
            initial_value=False,
            pin_factory=self._factory,
        )

        self._mark_state("LOW")
        print(f"[CompanionOps] GPIO ready on pin {self.deploy_pin_num}")

    async def close(self):
        async with self._lock:
            if self._deploy_pin is not None:
                try:
                    self._deploy_pin.off()
                finally:
                    self._deploy_pin.close()
                    self._deploy_pin = None

            self._mark_state("LOW")

    async def set_deploy(self, enabled: bool):
        async with self._lock:
            if self.dry_run:
                self._mark_state("HIGH" if enabled else "LOW")
                print(f"[CompanionOps] set_deploy({enabled})")
                return

            if self._deploy_pin is None:
                raise RuntimeError("CompanionOps not initialized")

            if enabled:
                self._deploy_pin.on()
                self._mark_state("HIGH")
            else:
                self._deploy_pin.off()
                self._mark_state("LOW")

    async def pulse_deploy(self, duration_s: float = 5.0):
        duration_s = max(0.0, float(duration_s))

        await self.set_deploy(True)
        try:
            await asyncio.sleep(duration_s)
        finally:
            await self.set_deploy(False)

    async def deploy_chemicals(self, duration_s: float = 5.0):
        await self.pulse_deploy(duration_s)
