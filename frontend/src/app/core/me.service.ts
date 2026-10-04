import { Injectable, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { ApiService } from './api.service';
import { Me } from './models';

/**
 * /api/me/ chargé une fois et partagé (nav + guard admin).
 * N'est qu'un confort d'affichage : la vraie barrière admin est côté backend (I3).
 */
@Injectable({ providedIn: 'root' })
export class MeService {
  private api = inject(ApiService);
  private pending?: Promise<Me | null>;

  readonly me = signal<Me | null>(null);

  load(): Promise<Me | null> {
    this.pending ??= firstValueFrom(this.api.getMe())
      .then(me => { this.me.set(me); return me; })
      .catch(() => { this.pending = undefined; return null; });
    return this.pending;
  }

  get isAdmin(): boolean {
    return this.me()?.is_admin === true;
  }
}
