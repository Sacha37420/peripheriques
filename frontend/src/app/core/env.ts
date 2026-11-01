/**
 * Configuration injectée au démarrage du conteneur (`assets/env.js`, écrit par
 * `nginx-entrypoint.sh`). Seul fichier du front à lire `window.__env`.
 */
export interface LabEnv {
  keycloakUrl?: string;
  keycloakRealm?: string;
  keycloakClientId?: string;
  appUrl?: string;
  apiUrl?: string;
}

export function labEnv(): LabEnv {
  return (window as unknown as { __env?: LabEnv }).__env ?? {};
}

/** Base de l'API de peripheriques (`…/peripheriques-api`), sans barre finale. */
export function apiUrl(): string {
  return (labEnv().apiUrl ?? 'http://localhost:8101').replace(/\/+$/, '');
}

/**
 * Les SEULES adresses auxquelles l'intercepteur joint le jeton de l'utilisateur
 * (`auth.interceptor.ts`, `token-scope.ts`) : l'API de peripheriques. Les
 * requêtes vers la brique lab-media (WHEP `/lab-media-webrtc/…`, HLS
 * `/lab-media-hls/…`) ne passent pas par HttpClient : le lecteur direct
 * (`shared/live-player`) y pose lui-même son en-tête Bearer. La relecture
 * passe par un ticket (`<video src>`), sans jeton.
 */
export function tokenBases(): string[] {
  return [apiUrl()];
}
