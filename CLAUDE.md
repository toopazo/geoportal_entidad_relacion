# Instrucciones para Claude

Al iniciar cualquier sesión en este repositorio, leer `notas/estado_proyecto.md` antes de responder.
Ese archivo es el log de sesiones y contiene el estado actual del proyecto, las decisiones tomadas y los próximos pasos.

## Repos relacionados

Este repo es el **coordinador**. Los repos de código viven en:

| Repo | Ruta local | Deploy |
|---|---|---|
| `geoportal-api` | `/home/toopazo/Dropbox/tomas/repos_git/geoportal-api/` | Render (Docker) |
| `geoportal-web` | `/home/toopazo/Dropbox/tomas/repos_git/geoportal-web/` | Vercel |

Claude puede leer y editar archivos en ambos repos usando rutas absolutas (herramientas Read/Edit directas, sin SSH).

Para cambios que afectan ambos lados (ej: nuevo endpoint), editar los dos repos en el mismo turno de conversación.
