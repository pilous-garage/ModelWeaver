export interface Dependency {
  name: string;
  description: string;
  check_command?: string;
  version_regex?: string;
  min_version?: string;
  install_commands?: Record<string, string>;
  installed?: boolean;
  version?: string;
  error?: string;
  // Champs issu du manifeste de dépendances
  language?: string;
  safe?: boolean;
  weight?: string;
  optional?: boolean;
  required?: boolean;
  target_pkg?: string;
}

export interface PackageManager {
  available: boolean;
  description: string;
}

export interface PythonPackageManager extends PackageManager {
  version?: string;
}
