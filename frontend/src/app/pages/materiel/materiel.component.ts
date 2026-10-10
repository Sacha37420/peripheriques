import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import {
  Continuous247, Materiel, MaterielFamily, MaterielItem, MaterielVerdict, apiErrorMessages,
} from '../../core/models';

const FAMILIES: { key: MaterielFamily; title: string; desc: string }[] = [
  { key: 'A', title: 'Famille A — sans fil via récepteur USB', desc: 'La caméra émet vers un récepteur branché sur l’hôte, vu comme une webcam (UVC).' },
  { key: 'B', title: 'Famille B — caméra IP Wi-Fi (RTSP)', desc: 'La caméra rejoint le réseau Wi-Fi et diffuse un flux RTSP.' },
  { key: 'C', title: 'Famille C — solution intermédiaire', desc: 'Montages hybrides ou passerelles.' },
];

const VERDICT_CLASS: Record<MaterielVerdict, string> = {
  'recommandé': 'badge-green',
  'possible': 'badge-yellow',
  'déconseillé pour ce service': 'badge-red',
};

const CONTINUOUS: Record<Continuous247, { cls: string; text: string }> = {
  'oui': { cls: 'c-yes', text: 'oui' },
  'non': { cls: 'c-no', text: 'non' },
  'seulement sur secteur': { cls: 'c-warn', text: 'seulement sur secteur' },
  'non confirmé': { cls: 'c-unknown', text: 'non confirmé' },
};

/**
 * Page admin « Matériel conseillé » (CONTRAT v6, I3 /api/admin/materiel/, format I7).
 * Aucun contenu produit n'est embarqué ici : tout vient de l'endpoint admin.
 */
@Component({
  selector: 'app-materiel',
  standalone: true,
  templateUrl: './materiel.component.html',
  styleUrl: './materiel.component.scss',
})
export class MaterielComponent implements OnInit {
  private api = inject(ApiService);

  readonly loading = signal(true);
  readonly unavailable = signal(false);
  readonly errors = signal<string[]>([]);
  readonly data = signal<Materiel | null>(null);

  readonly groups = computed(() => {
    const items = this.data()?.items ?? [];
    const known = FAMILIES.map(f => ({ ...f, items: items.filter(i => i.family === f.key) }));
    const other = items.filter(i => !FAMILIES.some(f => f.key === i.family));
    if (other.length) known.push({ key: 'C', title: 'Autres', desc: '', items: other });
    return known.filter(g => g.items.length);
  });

  readonly updatedLabel = computed(() => {
    const u = this.data()?.updated;
    if (!u || !/^\d{4}-\d{2}-\d{2}$/.test(u)) return u ?? '';
    const [y, m, d] = u.split('-').map(Number);
    return new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' })
      .format(new Date(Date.UTC(y, m - 1, d)));
  });

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.unavailable.set(false);
    this.errors.set([]);
    this.api.getMateriel().subscribe({
      next: d => { this.data.set(d); this.loading.set(false); },
      error: err => {
        if ((err as { status?: number })?.status === 503) this.unavailable.set(true);
        else this.errors.set(apiErrorMessages(err));
        this.loading.set(false);
      },
    });
  }

  verdictClass(v: MaterielVerdict): string {
    return VERDICT_CLASS[v] ?? 'badge-gray';
  }

  continuous(v: Continuous247): { cls: string; text: string } {
    return CONTINUOUS[v] ?? { cls: 'c-unknown', text: String(v) };
  }

  /** Seuls les liens https: sont rendus cliquables (I7) ; le reste est affiché en texte brut. */
  isHttps(url: string): boolean {
    try { return new URL(url).protocol === 'https:'; } catch { return false; }
  }

  trackItem(i: MaterielItem): string {
    return `${i.brand}|${i.name}`;
  }
}
