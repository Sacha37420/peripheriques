import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { MeService } from './me.service';

/** Masque la page Droits aux non-admins (confort UI — le backend refuse de toute façon). */
export const adminGuard: CanActivateFn = async () => {
  const me = await inject(MeService).load();
  return me?.is_admin === true ? true : inject(Router).parseUrl('/');
};
