import { Component, ElementRef, OnDestroy, OnInit, computed, inject, signal, viewChild } from '@angular/core';
import { Subscription } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { NoAccessComponent } from '../../shared/no-access/no-access.component';
import { Device, Segment, Ticket, apiErrorMessages } from '../../core/models';
import {
  addDays, fmtDateTime, fmtLongDate, fmtTime, parisDate, parisDayBounds,
} from '../../core/paris-time';

/** Fenêtre de conservation côté serveur (jours). */
const RETENTION_DAYS = 7;
const MAX_TICKET_S = 3600;
const HOUR = 3_600_000;

interface Block {
  seg: Segment;
  /** Bornes du segment ramenées à la journée (ms UTC). */
  from: number;
  to: number;
  left: number;
  width: number;
  cls: 'av' | 'video' | 'audio';
  label: string;
}

interface Tick { label: string; pos: number; }

interface Playback { start: number; ticket: Ticket | null; url: string; errors: string[]; }

function segClass(seg: Segment): 'av' | 'video' | 'audio' {
  const v = seg.tracks.includes('video');
  const a = seg.tracks.includes('audio');
  return v && a ? 'av' : v ? 'video' : 'audio';
}

/** ISO UTC sans millisecondes (« 2026-10-04T12:00:00Z »). */
function isoSeconds(ms: number): string {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** Instant (ms) de l'heure locale `h` de Paris dans la journée [b0, b1) — tient compte des changements d'heure. */
function localHour(b0: number, b1: number, h: number): number {
  if (h === 0) return b0;
  if (h === 24) return b1;
  const guess = b0 + h * HOUR;
  for (const d of [0, -HOUR, HOUR]) {
    if (Number(fmtTime(guess + d).slice(0, 2)) === h) return guess + d;
  }
  return guess;
}

@Component({
  selector: 'app-recordings',
  standalone: true,
  imports: [NoAccessComponent],
  templateUrl: './recordings.component.html',
  styleUrl: './recordings.component.scss',
})
export class RecordingsComponent implements OnInit, OnDestroy {
  private api = inject(ApiService);

  private media = viewChild<ElementRef<HTMLMediaElement>>('media');
  private segSub?: Subscription;
  private ticketSub?: Subscription;

  readonly fmtTime = fmtTime;
  readonly fmtDateTime = fmtDateTime;

  readonly maxDate = signal(parisDate());
  readonly minDate = computed(() => addDays(this.maxDate(), -RETENTION_DAYS));

  readonly loadingDevices = signal(true);
  readonly deviceErrors = signal<string[]>([]);
  readonly devices = signal<Device[]>([]);
  readonly slug = signal<string>('');
  readonly date = signal<string>(parisDate());

  readonly loadingSegs = signal(false);
  readonly segErrors = signal<string[]>([]);
  readonly segments = signal<Segment[]>([]);

  readonly playback = signal<Playback | null>(null);

  readonly device = computed(() => this.devices().find(d => d.slug === this.slug()) ?? null);
  readonly longDate = computed(() => fmtLongDate(this.date()));
  readonly bounds = computed(() => parisDayBounds(this.date()));

  readonly ticks = computed<Tick[]>(() => {
    const [b0, b1] = this.bounds();
    return [0, 6, 12, 18, 24].map(h => ({
      label: `${h}h`,
      pos: ((localHour(b0, b1, h) - b0) / (b1 - b0)) * 100,
    }));
  });

  readonly blocks = computed<Block[]>(() => {
    const [b0, b1] = this.bounds();
    const len = b1 - b0;
    const out: Block[] = [];
    for (const seg of this.segments()) {
      const from = Math.max(Date.parse(seg.start), b0);
      const to = Math.min(Date.parse(seg.end), b1);
      if (!(to > from)) continue;
      out.push({
        seg, from, to,
        left: ((from - b0) / len) * 100,
        width: ((to - from) / len) * 100,
        cls: segClass(seg),
        label: `Lire l’enregistrement de ${fmtTime(from)} à ${fmtTime(to)}`,
      });
    }
    return out;
  });

  /**
   * Phrases du bandeau « début d'historique ». CONTRAT v3 : un droit accordé a toujours un
   * `since` (admin compris — aucun accès implicite) ; null ⇔ allowed=false.
   */
  readonly sinceLines = computed<string[]>(() => {
    const d = this.device();
    if (!d) return [];
    const caps = [
      { name: 'Vidéo', cap: d.video },
      { name: 'Micro', cap: d.audio },
    ].filter((c): c is { name: string; cap: { allowed: true; since: string } } => c.cap.allowed && !!c.cap.since);
    if (!caps.length) return [];
    const lines: string[] = [];
    if (caps.length === 2 && caps[0].cap.since === caps[1].cap.since) {
      lines.push(`Votre historique commence le ${fmtDateTime(caps[0].cap.since)}.`);
    } else {
      for (const c of caps) lines.push(`${c.name} disponible depuis le ${fmtDateTime(c.cap.since)}.`);
    }
    lines.push('Les enregistrements antérieurs à l’octroi de votre droit ne sont pas accessibles.');
    return lines;
  });

  ngOnInit(): void {
    this.api.getDevices().subscribe({
      next: devices => {
        const usable = devices.filter(d => d.video.allowed || d.audio.allowed);
        this.devices.set(usable);
        this.loadingDevices.set(false);
        if (usable.length) {
          this.slug.set(usable[0].slug);
          this.loadSegments();
        }
      },
      error: err => {
        this.deviceErrors.set(apiErrorMessages(err));
        this.loadingDevices.set(false);
      },
    });
  }

  ngOnDestroy(): void {
    this.segSub?.unsubscribe();
    this.stop();
  }

  onDevice(slug: string): void {
    if (slug === this.slug()) return;
    this.slug.set(slug);
    this.reload();
  }

  onDate(value: string): void {
    if (!value || value === this.date()) return;
    if (value < this.minDate() || value > this.maxDate()) return;
    this.date.set(value);
    this.reload();
  }

  shiftDay(n: number): void {
    this.onDate(addDays(this.date(), n));
  }

  private reload(): void {
    this.stop();
    this.loadSegments();
  }

  loadSegments(): void {
    const slug = this.slug();
    if (!slug) return;
    this.segSub?.unsubscribe();
    this.loadingSegs.set(true);
    this.segErrors.set([]);
    this.segments.set([]);
    this.segSub = this.api.getRecordings(slug, this.date()).subscribe({
      next: segs => {
        this.segments.set([...segs].sort((a, b) => Date.parse(a.start) - Date.parse(b.start)));
        this.loadingSegs.set(false);
      },
      error: err => {
        this.segErrors.set(apiErrorMessages(err));
        this.loadingSegs.set(false);
      },
    });
  }

  /** Clic sur la frise : lecture à partir de l'instant pointé. */
  playFromBlock(b: Block, ev: MouseEvent): void {
    let frac = 0;
    // detail === 0 : activation clavier (Entrée/Espace) → début du bloc.
    if (ev.detail !== 0) {
      const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
      frac = rect.width > 0 ? Math.min(1, Math.max(0, (ev.clientX - rect.left) / rect.width)) : 0;
    }
    const segEnd = Date.parse(b.seg.end);
    let start = Math.round((b.from + frac * (b.to - b.from)) / 1000) * 1000;
    if (start < b.from) start = Math.ceil(b.from / 1000) * 1000;
    if (segEnd - start < 1000) start = Math.max(b.from, segEnd - 1000);
    this.play(start, Math.floor((segEnd - start) / 1000));
  }

  playSegment(seg: Segment): void {
    this.play(Date.parse(seg.start), Math.floor(seg.duration_s));
  }

  private play(startMs: number, seconds: number): void {
    this.stop();
    const duration_s = Math.max(1, Math.min(MAX_TICKET_S, seconds));
    this.playback.set({ start: startMs, ticket: null, url: '', errors: [] });
    this.ticketSub = this.api.createTicket({
      device: this.slug(), start: isoSeconds(startMs), duration_s,
    }).subscribe({
      next: ticket => this.playback.set({
        start: startMs, ticket, url: this.api.ticketUrl(ticket), errors: [],
      }),
      error: err => this.playback.set({
        start: startMs, ticket: null, url: '', errors: apiErrorMessages(err),
      }),
    });
  }

  /** Arrête la lecture, libère la connexion et retire le lecteur. */
  stop(): void {
    this.ticketSub?.unsubscribe();
    this.ticketSub = undefined;
    const el = this.media()?.nativeElement;
    if (el) {
      el.pause();
      el.removeAttribute('src');
      el.load();
    }
    this.playback.set(null);
  }

  hasVideo(t: Ticket): boolean {
    return t.tracks.includes('video');
  }

  fmtDuration(s: number): string {
    const total = Math.round(s);
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    if (h) return `${h} h ${String(m).padStart(2, '0')} min`;
    if (m) return `${m} min`;
    return `${total} s`;
  }
}
