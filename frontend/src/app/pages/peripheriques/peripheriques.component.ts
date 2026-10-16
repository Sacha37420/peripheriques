import { Component, OnInit, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import {
  AdminDevice, AdminDeviceList, AdminDevicePatch, DeviceState, STATE_LABEL, apiErrorMessages,
} from '../../core/models';
import { fmtDateTime } from '../../core/paris-time';

const LABEL_MAX = 60;

const STATE_CLASS: Record<DeviceState, string> = {
  live: 'badge-green',
  waiting: 'badge-yellow',
  starting: 'badge-blue',
  error: 'badge-red',
  offline: 'badge-gray',
  legacy: 'badge-gray',
  '': 'badge-gray',
};

/**
 * Page « Périphériques » (administrateurs des droits) : tous les périphériques déjà vus par
 * lab-media, branchés ou non. Renommer (nom d'affichage, texte brut), interrupteur
 * d'enregistrement (relayé à lab-media), oublier un périphérique débranché.
 * Tout texte venu de l'API (nom, modèle, identifiant) est rendu par interpolation.
 */
@Component({
  selector: 'app-peripheriques',
  standalone: true,
  templateUrl: './peripheriques.component.html',
  styleUrl: './peripheriques.component.scss',
})
export class PeripheriquesComponent implements OnInit {
  private api = inject(ApiService);

  readonly labelMax = LABEL_MAX;
  readonly stateLabel = STATE_LABEL;
  readonly stateClass = STATE_CLASS;

  readonly loading = signal(true);
  readonly loadErrors = signal<string[]>([]);
  readonly inventory = signal<AdminDeviceList['inventory'] | null>(null);
  readonly devices = signal<AdminDevice[]>([]);

  readonly busy = signal<ReadonlySet<string>>(new Set());
  readonly errors = signal<Record<string, string[]>>({});
  readonly notices = signal<Record<string, string>>({});
  readonly drafts = signal<Record<string, string>>({});
  readonly confirming = signal<string | null>(null);

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.loadErrors.set([]);
    this.api.getAdminDevices().subscribe({
      next: data => {
        this.inventory.set(data.inventory);
        this.devices.set(data.items);
        this.drafts.set(Object.fromEntries(data.items.map(d => [d.id, d.label])));
        this.errors.set({});
        this.notices.set({});
        this.confirming.set(null);
        this.loading.set(false);
      },
      error: err => {
        this.loadErrors.set(apiErrorMessages(err));
        this.loading.set(false);
      },
    });
  }

  fmt(iso: string | null): string {
    return iso ? fmtDateTime(iso) : '—';
  }

  isBusy(id: string): boolean {
    return this.busy().has(id);
  }

  draft(d: AdminDevice): string {
    return this.drafts()[d.id] ?? d.label;
  }

  setDraft(id: string, value: string): void {
    this.drafts.update(m => ({ ...m, [id]: value }));
  }

  /** Message d'erreur local du nom saisi, ou '' s'il est acceptable. */
  draftError(d: AdminDevice): string {
    const v = this.draft(d).trim();
    if (!v) return 'Le nom ne peut pas être vide.';
    if (v.length > LABEL_MAX) return `${LABEL_MAX} caractères au plus.`;
    // Confort : l'API refuse de toute façon (400) un nom déjà porté, casse comprise.
    const key = v.toLowerCase();
    if (this.devices().some(o => o.id !== d.id && o.label.trim().toLowerCase() === key)) {
      return 'Ce nom est déjà porté par un autre périphérique.';
    }
    return '';
  }

  canRename(d: AdminDevice): boolean {
    return !this.isBusy(d.id) && !this.draftError(d) && this.draft(d).trim() !== d.label;
  }

  // ── Renommer ──
  rename(d: AdminDevice, event: Event): void {
    event.preventDefault();
    if (!this.canRename(d)) return;
    this.send(d, { label: this.draft(d).trim() }, 'Nom enregistré.');
  }

  // ── Interrupteur d'enregistrement ──
  toggleRecord(d: AdminDevice, event: Event): void {
    const input = event.target as HTMLInputElement;
    const next = input.checked;
    if (this.isBusy(d.id) || next === d.record) {
      input.checked = d.record;
      return;
    }
    this.send(d, { record: next },
      next ? 'Enregistrement activé.' : 'Enregistrement arrêté.',
      () => { input.checked = d.record; });
  }

  // ── Oublier ──
  askForget(d: AdminDevice): void {
    this.setErrors(d.id, []);
    this.confirming.set(d.id);
  }

  cancelForget(): void {
    this.confirming.set(null);
  }

  forget(d: AdminDevice): void {
    if (this.isBusy(d.id)) return;
    this.setBusy(d.id, true);
    this.setErrors(d.id, []);
    this.api.forgetAdminDevice(d.id).subscribe({
      next: () => {
        this.devices.update(list => list.filter(x => x.id !== d.id));
        this.confirming.set(null);
        this.setBusy(d.id, false);
      },
      error: err => {
        this.setErrors(d.id, apiErrorMessages(err));
        this.setBusy(d.id, false);
      },
    });
  }

  // ── Utilitaires ──
  private send(d: AdminDevice, body: AdminDevicePatch, ok: string, onError?: () => void): void {
    this.setBusy(d.id, true);
    this.setErrors(d.id, []);
    this.setNotice(d.id, '');
    this.api.updateAdminDevice(d.id, body).subscribe({
      next: updated => {
        this.devices.update(list => list.map(x => (x.id === updated.id ? updated : x)));
        if ('label' in body) this.setDraft(updated.id, updated.label);
        this.setNotice(d.id, ok);
        this.setBusy(d.id, false);
      },
      error: err => {
        onError?.();
        this.setErrors(d.id, apiErrorMessages(err));
        this.setBusy(d.id, false);
      },
    });
  }

  private setBusy(id: string, on: boolean): void {
    this.busy.update(s => {
      const n = new Set(s);
      if (on) n.add(id); else n.delete(id);
      return n;
    });
  }

  private setErrors(id: string, msgs: string[]): void {
    this.errors.update(r => {
      const n = { ...r };
      if (msgs.length) n[id] = msgs; else delete n[id];
      return n;
    });
  }

  private setNotice(id: string, msg: string): void {
    this.notices.update(r => {
      const n = { ...r };
      if (msg) n[id] = msg; else delete n[id];
      return n;
    });
  }
}
