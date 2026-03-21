# companion_ops.py
import asyncio

try:
    from gpiozero import OutputDevice
except Exception:
    OutputDevice = None


class CompanionOps:
    """
    Raspberry Pi / companion-computer-only operations.
    Keep all GPIO, local sensors, relays, LEDs, buzzers, etc. here.
    """

    def __init__(self, deploy_pin=18, active_high=True, dry_run=False):
        self.deploy_pin_num = deploy_pin
        self.active_high = active_high
        self.dry_run = dry_run

        self._deploy_pin = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """
        Initialize local hardware resources.
        """
        if self.dry_run:
            print(f"[CompanionOps] DRY RUN enabled, deploy_pin={self.deploy_pin_num}")
            return

        if OutputDevice is None:
            raise RuntimeError(
                "gpiozero is not available. Install it or run with dry_run=True."
            )

        self._deploy_pin = OutputDevice(
            self.deploy_pin_num,
            active_high=self.active_high,
            initial_value=False
        )
        print(f"[CompanionOps] GPIO ready on pin {self.deploy_pin_num}")

    async def close(self):
        """
        Cleanup local hardware safely.
        """
        async with self._lock:
            if self._deploy_pin is not None:
                try:
                    self._deploy_pin.off()
                finally:
                    self._deploy_pin.close()
                    self._deploy_pin = None
        print("[CompanionOps] GPIO cleaned up")

    def _ensure_ready(self):
        if self.dry_run:
            return
        if self._deploy_pin is None:
            raise RuntimeError("CompanionOps not initialized. Call connect() first.")

    async def set_deploy(self, enabled: bool):
        """
        Turn deploy output on/off directly.
        """
        async with self._lock:
            self._ensure_ready()

            if self.dry_run:
                print(f"[CompanionOps] set_deploy(enabled={enabled})")
                return

            if enabled:
                self._deploy_pin.on()
            else:
                self._deploy_pin.off()

    async def pulse_deploy(self, duration_s: float = 5.0):
        """
        Turn deploy output on for duration_s, then turn it off.
        """
        duration_s = max(0.0, float(duration_s))

        async with self._lock:
            self._ensure_ready()

            if self.dry_run:
                print(f"[CompanionOps] pulse_deploy({duration_s}s)")
                await asyncio.sleep(duration_s)
                return

            self._deploy_pin.on()
            try:
                await asyncio.sleep(duration_s)
            finally:
                self._deploy_pin.off()

    async def deploy_chemicals(self, duration_s: float = 5.0):
        """
        Higher-level API for chemical deployment relay/actuator.
        """
        await self.pulse_deploy(duration_s=duration_s)
