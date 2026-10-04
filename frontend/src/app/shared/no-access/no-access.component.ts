import { Component, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MeService } from '../../core/me.service';

/**
 * Message « aucun droit », identique pour tous (CONTRAT v3 : être admin ne donne
 * aucun accès implicite). Un admin reçoit en plus un lien vers la page Droits pour
 * se donner un droit — qui ne vaut qu'à partir de son octroi.
 */
@Component({
  selector: 'app-no-access',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="card empty" data-test="no-access">
      <p class="empty-title">Aucun périphérique ne vous est accessible.</p>
      <p class="empty-msg">Un administrateur doit vous accorder un droit (vidéo, micro ou les deux) sur un périphérique.</p>
      @if (me.me()?.is_admin) {
        <p class="admin-hint">
          Vous êtes administrateur : vous pouvez vous accorder un droit à votre propre email.
          Il ne vaudra qu’à partir de son octroi — les enregistrements antérieurs resteront inaccessibles.
        </p>
        <a routerLink="/droits" class="btn btn-primary" data-test="no-access-grants-link">Ouvrir la page Droits</a>
      }
    </div>
  `,
  styles: [`
    .empty { text-align: center; display: flex; flex-direction: column; align-items: center; gap: var(--space-2); }
    .empty-title { font-weight: 600; padding-top: var(--space-4); }
    .admin-hint { color: var(--text-mute); font-size: var(--text-sm); max-width: 60ch; }
    .btn { margin-bottom: var(--space-4); }
  `],
})
export class NoAccessComponent {
  protected me = inject(MeService);

  constructor() {
    void this.me.load();
  }
}
