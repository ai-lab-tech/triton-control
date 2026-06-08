import { loginFeature } from "./login.reducer";

export const {
  selectOidcEnabled: selectLoginOidcEnabled,
  selectNeedsBootstrap: selectLoginNeedsBootstrap,
  selectLoading: selectLoginLoading,
  selectError: selectLoginError,
  selectNotice: selectLoginNotice,
  selectRegisterMode: selectLoginRegisterMode,
} = loginFeature;
