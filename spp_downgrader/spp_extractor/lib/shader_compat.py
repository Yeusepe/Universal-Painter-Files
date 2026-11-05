"""Legacy shader fallback, authored against Painter's public shader API.

This uses the target's built-in libraries; no Adobe shader source is bundled.
"""
import re


OPENPBR_LOSS = (
    "OpenPBR shaders will use a legacy metal/rough fallback. Base color, metallic, "
    "roughness, normals, height, emissive and opacity remain available; "
    "OpenPBR-specific shading and appearance controls are lost."
)


def uses_openpbr(raw):
    try:
        source = raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        return False
    source = re.sub(r'/\*.*?\*/|//[^\r\n]*', '', source, flags=re.S)
    return bool(re.search(r'^\s*import\s+lib-openpbr-[\w-]+\.glsl\b', source, re.M))


# Keep the OpenPBR opacity/culling parameter names so saved per-shader values
# still control the fallback. Newer material lobes have no legacy equivalent.
LEGACY_OPENPBR_SHADER = b'''// Universal SPP legacy metal/rough fallback
import lib-pbr.glsl
import lib-pom.glsl
import lib-utils.glsl
import lib-alpha-test.glsl

//: metadata {"mdl":"mdl::alg::materials::painter::pbr::physically_metallic_roughness"}
//: param auto channel_basecolor
uniform SamplerSparse uspp_color;
//: param auto channel_metallic
uniform SamplerSparse uspp_metal;
//: param auto channel_roughness
uniform SamplerSparse uspp_rough;
//: param auto channel_specularlevel
uniform SamplerSparse uspp_specular;
//: param custom {"default":false,"label":"Alpha blending"}
uniform_specialization bool alphaBlendEnabled;
//: param custom {"default":false,"label":"Double sided"}
uniform_specialization bool doubleSided;
//: state blend over {"enable":"input.alphaBlendEnabled"}
//: state cull_face off {"enable":"input.doubleSided"}

void shade(V2F inputs) {
    applyParallaxOffset(inputs, worldSpaceToTangentSpace(getEyeVec(inputs.position), inputs));
    LocalVectors frame = computeLocalFrame(inputs);
    vec3 color = getBaseColor(uspp_color, inputs.sparse_coord);
    float metal = getMetallic(uspp_metal, inputs.sparse_coord);
    float rough = getRoughness(uspp_rough, inputs.sparse_coord);
    vec3 reflection = generateSpecularColor(
        getSpecularLevel(uspp_specular, inputs.sparse_coord), color, metal);
    float visibility = getAO(inputs.sparse_coord) * getShadowFactor();
    diffuseShadingOutput(visibility * pbrComputeDiffuse(
        frame.normal, generateDiffuseColor(color, metal)));
    specularShadingOutput(pbrComputeSpecular(frame, reflection, rough)
        * specularOcclusionCorrection(visibility, metal, rough));
    emissiveColorOutput(pbrComputeEmissive(emissive_tex, inputs.sparse_coord));
    float opacity = getOpacity(opacity_tex, inputs.sparse_coord);
    alphaOutput(alphaBlendEnabled ? opacity : 1.0);
    alphaKill(inputs.sparse_coord);
}
'''
