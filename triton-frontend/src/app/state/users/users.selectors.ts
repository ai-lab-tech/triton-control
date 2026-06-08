import { usersFeature } from "./users.reducer";

export const {
  selectUsers,
  selectInstances: selectUsersInstances,
  selectOidcEnabled: selectUsersOidcEnabled,
  selectLoading: selectUsersLoading,
  selectError: selectUsersError,
} = usersFeature;
