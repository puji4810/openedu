import path from 'node:path'

/** True when `dir` lives inside a packaged app bundle / install tree. */
function isPackagedInstallPath(dir, { installRoots, isPackaged }: { installRoots: string[]; isPackaged: boolean }) {
  if (!isPackaged || !dir) {
    return false
  }

  let resolved

  try {
    resolved = path.resolve(String(dir))
  } catch {
    return false
  }

  const roots = new Set((installRoots ?? []).filter(Boolean).map(candidate => path.resolve(String(candidate))))

  for (const root of roots) {
    if (resolved === root) {
      return true
    }

    const rel = path.relative(root, resolved) as any

    if (rel && !rel.startsWith('..') && !path.isAbsolute(rel)) {
      return true
    }
  }

  return false
}

export { isPackagedInstallPath }

interface WorkspaceCwdInput {
  explicitCwd?: string
  defaultProjectDir?: string
  initCwd?: string
  processCwd?: string
  sourceRepoRoot?: string
  homeDir: string
  isPackaged: boolean
  installRoots?: string[]
  directoryExists?: (dir: string) => boolean
}

export function resolveWorkspaceCwd({
  explicitCwd,
  defaultProjectDir,
  initCwd,
  processCwd,
  sourceRepoRoot,
  homeDir,
  isPackaged,
  installRoots,
  directoryExists
}: WorkspaceCwdInput) {
  const exists = typeof directoryExists === 'function' ? directoryExists : () => false
  const candidates = [
    explicitCwd,
    defaultProjectDir,
    isPackaged ? null : initCwd,
    isPackaged ? null : processCwd,
    !isPackaged ? sourceRepoRoot : null,
    homeDir
  ]

  for (const candidate of candidates) {
    if (!candidate) {
      continue
    }

    const resolved = path.resolve(String(candidate))

    if (isPackagedInstallPath(resolved, { installRoots, isPackaged })) {
      continue
    }

    if (exists(resolved)) {
      return resolved
    }
  }

  return homeDir
}
