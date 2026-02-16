from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from livekit.agents import metrics, MetricsCollectedEvent


@dataclass
class TurnMetrics:
    """Track metrics for a single conversation turn"""
    turn_id: int
    timestamp: datetime
    stt_duration: Optional[float] = None
    eou_delay: Optional[float] = None
    llm_ttft: Optional[float] = None
    llm_duration: Optional[float] = None
    llm_tokens: Optional[int] = None
    tts_ttfb: Optional[float] = None
    tts_duration: Optional[float] = None
    tts_characters: Optional[int] = None

    @property
    def total_latency(self) -> float:
        """Calculate total turn latency in ms"""
        total = 0.0
        if self.stt_duration:
            total += self.stt_duration
        if self.llm_duration:
            total += self.llm_duration
        if self.tts_duration:
            total += self.tts_duration
        return total

    def print_summary(self):
        """Print turn metrics"""
        print(f"\n📊 Turn {self.turn_id} Metrics ({self.timestamp.strftime('%H:%M:%S')})")
        print("─" * 60)
        if self.stt_duration:
            print(f"  STT Duration:          {self.stt_duration:>8.0f} ms")
        if self.eou_delay:
            print(f"  EOU Delay:             {self.eou_delay:>8.0f} ms")
        if self.llm_ttft:
            print(f"  LLM TTFT:              {self.llm_ttft:>8.0f} ms ⭐")
        if self.llm_duration:
            print(f"  LLM Duration:          {self.llm_duration:>8.0f} ms")
        if self.llm_tokens:
            print(f"  LLM Tokens:            {self.llm_tokens:>8} tokens")
        if self.tts_ttfb:
            print(f"  TTS TTFB:              {self.tts_ttfb:>8.0f} ms")
        if self.tts_duration:
            print(f"  TTS Duration:          {self.tts_duration:>8.0f} ms")
        if self.tts_characters:
            print(f"  TTS Characters:        {self.tts_characters:>8} chars")
        print(f"  TOTAL LATENCY:         {self.total_latency:>8.0f} ms")
        print("─" * 60)


class MetricsTracker:
    """Track metrics using Official LiveKit metrics"""

    def __init__(self):
        self.usage_collector = metrics.UsageCollector()
        self.turns: dict[int, TurnMetrics] = {}
        self.current_turn_id = 0
        self.alerts = []

        # Thresholds for alerting
        self.thresholds = {
            "stt_duration": 500,  # ms
            "llm_ttft": 1000,  # ms (most important)
            "llm_duration": 2000,  # ms
            "tts_duration": 600,  # ms
            "total_latency": 3000,  # ms
        }

    def start_turn(self) -> int:
        """Start a new conversation turn"""
        self.current_turn_id += 1
        self.turns[self.current_turn_id] = TurnMetrics(
            turn_id=self.current_turn_id,
            timestamp=datetime.now()
        )
        return self.current_turn_id

    def on_metrics(self, ev: MetricsCollectedEvent):
        """
        Handle metrics from LiveKit.
        This is called automatically when metrics are emitted.
        """
        # Log metrics automatically (this is built-in)
        metrics.log_metrics(ev.metrics)

        # Collect for usage tracking (billing)
        self.usage_collector.collect(ev.metrics)

        # Process metrics based on type
        metric = ev.metrics

        if self.current_turn_id == 0:
            self.start_turn()

        turn = self.turns[self.current_turn_id]

        # STT Metrics
        if isinstance(metric, metrics.STTMetrics):
            turn.stt_duration = metric.duration
            print(f"\n🎤 STT: {metric.duration:.0f}ms "
                  f"({metric.audio_duration:.0f}ms audio)")
            self._check_threshold("stt_duration", metric.duration)

        # End-of-Utterance Metrics
        elif isinstance(metric, metrics.EOUMetrics):
            turn.eou_delay = metric.end_of_utterance_delay
            print(f"🎤 EOU detected: {metric.end_of_utterance_delay:.0f}ms delay")

        # LLM Metrics (Most Important for User Experience)
        elif isinstance(metric, metrics.LLMMetrics):
            turn.llm_ttft = metric.ttft
            turn.llm_duration = metric.duration
            turn.llm_tokens = metric.total_tokens

            print(f"\n🧠 LLM Response:")
            print(f"   TTFT (Time-to-First-Token): {metric.ttft:.0f}ms ⭐")
            print(f"   Duration: {metric.duration:.0f}ms")
            print(f"   Tokens: {metric.total_tokens} ({metric.tokens_per_second:.2f}/sec)")

            # Check thresholds
            self._check_threshold("llm_ttft", metric.ttft)
            self._check_threshold("llm_duration", metric.duration)

            # Check total turn latency
            self._check_threshold("total_latency", turn.total_latency)

            # Start new turn for next user input
            self.start_turn()

        # TTS Metrics
        elif isinstance(metric, metrics.TTSMetrics):
            turn.tts_ttfb = metric.ttfb
            turn.tts_duration = metric.duration
            turn.tts_characters = metric.characters_count

            print(f"\n🔊 TTS Synthesis:")
            print(f"   TTFB (Time-to-First-Byte): {metric.ttfb:.0f}ms")
            print(f"   Duration: {metric.duration:.0f}ms")
            print(f"   Audio: {metric.audio_duration:.0f}ms ({metric.characters_count} chars)")

            self._check_threshold("tts_duration", metric.duration)

        # VAD Metrics
        elif isinstance(metric, metrics.VADMetrics):
            print(f"🎙️  VAD: {metric.inference_count} inferences, "
                  f"{metric.idle_time:.0f}ms idle")

    def _check_threshold(self, metric_name: str, value: float):
        """Check if metric exceeds threshold"""
        threshold = self.thresholds.get(metric_name)
        if threshold and value > threshold:
            alert = f"⚠️  {metric_name} exceeded threshold: {value:.0f}ms > {threshold:.0f}ms"
            self.alerts.append(alert)
            print(f"\n{alert}")

    def print_session_summary(self):
        """Print final session summary with all metrics"""
        print("\n\n" + "=" * 70)
        print("📊 SESSION METRICS SUMMARY")
        print("=" * 70)

        # Print all turns
        turn_count = 0
        for turn in self.turns.values():
            if turn.stt_duration or turn.llm_duration or turn.tts_duration:
                turn.print_summary()
                turn_count += 1

        # Print usage summary
        summary = self.usage_collector.get_summary()
        print("\n📈 USAGE SUMMARY (For Billing)")
        print("─" * 70)
        print(f"LLM Prompt Tokens:       {summary.llm_prompt_tokens:>10}")
        print(f"LLM Completion Tokens:   {summary.llm_completion_tokens:>10}")
        print(f"Total LLM Tokens:        {summary.llm_prompt_tokens + summary.llm_completion_tokens:>10}")
        print(f"STT Audio Duration:      {summary.stt_audio_duration:>10.1f} seconds")
        print(f"TTS Characters:          {summary.tts_characters_count:>10}")
        print(f"TTS Audio Duration:      {summary.tts_audio_duration:>10.1f} seconds")
        print("─" * 70)

        # Cost estimation
        llm_cost = (summary.llm_prompt_tokens * 0.0005 +
                    summary.llm_completion_tokens * 0.0015) / 1000
        stt_cost = summary.stt_audio_duration * 0.025 / 60
        tts_cost = summary.tts_characters_count * 0.000015
        total_cost = llm_cost + stt_cost + tts_cost

        print("\n💰 COST ESTIMATION (Example Rates)")
        print("─" * 70)
        print(f"LLM Cost:                ${llm_cost:>10.6f}")
        print(f"STT Cost:                ${stt_cost:>10.6f}")
        print(f"TTS Cost:                ${tts_cost:>10.6f}")
        print(f"Total Cost:              ${total_cost:>10.6f}")
        print("─" * 70)

        # Alerts summary
        if self.alerts:
            print("\n⚠️  ALERTS")
            print("─" * 70)
            for alert in self.alerts:
                print(f"  {alert}")
            print("─" * 70)

        # Statistics
        turn_latencies = [
            turn.total_latency for turn in self.turns.values()
            if turn.total_latency > 0
        ]
        if turn_latencies:
            print("\n📊 LATENCY STATISTICS")
            print("─" * 70)
            print(f"Turns Completed:         {len(turn_latencies):>10}")
            print(f"Min Latency:             {min(turn_latencies):>10.0f} ms")
            print(f"Max Latency:             {max(turn_latencies):>10.0f} ms")
            print(f"Avg Latency:             {sum(turn_latencies) / len(turn_latencies):>10.0f} ms")
            print("─" * 70)

        print("=" * 70)
