"""HBO reserializer: decode a source HBO stream and rewrite it for a target version.

This was one 2500-line module; it is now a package split by responsibility. The public
surface is unchanged -- `from lib.hbo_reserializer import HBOSerializer` still works, and
`lib.hbo_reserializer.runtime` holds the profile-derived state. uspp_tool reloads this
package per target, which re-runs runtime.bind() below to pick up the new active profile.

Submodules: runtime (state), models (MemberDef/ObjectDef + size loader), serializer
(class attrs, __init__, transcode orchestration) composing the mixins _readers,
_write_inline, _write_registry, _transforms, _schema, _helpers.
"""
from . import runtime
runtime.bind()

from .serializer import HBOSerializer  # noqa: E402  (must follow runtime.bind())

__all__ = ["HBOSerializer", "runtime"]
