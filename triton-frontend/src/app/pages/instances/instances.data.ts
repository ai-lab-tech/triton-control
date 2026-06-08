export type InstanceAssignedUser = {
  name: string;
  role: string;
};

export type InstanceRepositoryModel = {
  name: string;
  version: string;
  state: string;
  reason: string;
};

export type Instance = {
  id: string;
  name: string;
  url: string;
  status: "healthy" | "warning" | "down";
  version: string;
  region: string;
  models: number;
  healthLive: boolean;
  healthReady: boolean;
  healthLastCheckedAt: string;
  healthError: string;
  tritonVerifySsl: boolean;
  tritonCaCertificate: string;
  metricsUrl: string;
  metricsLastCheckedAt: string;
  metricsError: string;
  deploymentRuntime: string;
  deploymentNamespace: string;
  deploymentName: string;
  deploymentServiceName: string;
  deploymentSecretName: string;
  deploymentLog: string;
  isSelfDeployed: boolean;
  podStatuses: string[];
  serverMetadata: Record<string, unknown> | null;
  qps: number;
  cpu: number;
  ram: number;
  gpu: number;
  assignedUsers: InstanceAssignedUser[];
  s3: {
    enabled: boolean;
    bucket: string;
    region: string;
    endpoint: string;
    prefix: string;
    accessKey: string;
    secretConfigured: boolean;
    useHttps: boolean;
    verifySsl: boolean;
    caCertificate: string;
  };
  modelFiles: { name: string; type: "folder" | "file"; level: number }[];
  repositoryModels: InstanceRepositoryModel[];
};
