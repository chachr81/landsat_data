# 🛰️ Bandas QA de Landsat Collection 2 - Guía de Enmascaramiento

## Bandas QA Disponibles en Landsat Collection 2 Level-2

### 1. **QA_PIXEL** (Banda Principal para Enmascaramiento)
**Archivo:** `*_QA_PIXEL.TIF`  
**Tipo:** 16-bit unsigned integer (uint16)  
**Uso:** Detección de nubes, sombras, nieve, agua y calidad de pixel

#### Estructura de Bits (Binary Flags)
```
Bit 0: Fill (1 = pixel sin datos)
Bit 1: Dilated Cloud (1 = nube dilatada, buffer de seguridad)
Bit 2: Cirrus (High confidence) - Solo Landsat 8/9
Bit 3: Cloud (1 = nube detectada)
Bit 4: Cloud Shadow (1 = sombra de nube)
Bit 5: Snow (1 = nieve/hielo)
Bit 6: Clear (1 = pixel claro, sin obstrucciones)
Bit 7: Water (1 = agua detectada por algoritmo interno)
Bits 8-9: Cloud Confidence (00=Not Set, 01=Low, 10=Medium, 11=High)
Bits 10-11: Cloud Shadow Confidence
Bits 12-13: Snow/Ice Confidence
Bits 14-15: Cirrus Confidence (Solo Landsat 8/9)
```

#### Máscara Recomendada para MNDWI (Detección de Agua)
```python
# Valores a ENMASCARAR (excluir del análisis):
# - Fill (bit 0 = 1) → valor 1
# - Dilated Cloud (bit 1 = 1) → valor 2
# - Cloud (bit 3 = 1) → valor 8
# - Cloud Shadow (bit 4 = 1) → valor 16

# Máscara combinada (bits 0, 1, 3, 4):
qa_mask = (qa_pixel & 0b0000000000011011) == 0

# Alternativamente, solo excluir nubes y fill:
qa_simple_mask = (qa_pixel & 0b0000000000001001) == 0
```

---

### 2. **QA_RADSAT** (Saturación Radiométrica)
**Archivo:** `*_QA_RADSAT.TIF`  
**Tipo:** 16-bit unsigned integer  
**Uso:** Detectar bandas saturadas (valores fuera de rango)

```
Bit 0: Band 1 saturated
Bit 1: Band 2 saturated
Bit 2: Band 3 saturated (Green) - ¡IMPORTANTE PARA MNDWI!
...
Bit 5: Band 6 saturated (SWIR) - ¡IMPORTANTE PARA MNDWI!
```

**Uso:** Excluir píxeles donde B3 o B6 estén saturados:
```python
# Verificar saturación en B3 (bit 2) y B6 (bit 5)
radsat_mask = (qa_radsat & 0b0000000000100100) == 0
```

---

### 3. **QA_AEROSOL** (Calidad Atmosférica)
**Archivo:** `*_QA_AEROSOL.TIF`  
**Tipo:** 8-bit unsigned integer  
**Uso:** Nivel de aerosoles atmosféricos (opcional para MNDWI)

```
Bits 6-7: Aerosol Level
  00 = Climatology aerosol
  01 = Low aerosol
  10 = Medium aerosol
  11 = High aerosol
```

**Uso (opcional):** Filtrar escenas con alta contaminación atmosférica:
```python
aerosol_level = (qa_aerosol >> 6) & 0b11
# Excluir si aerosol_level == 3 (alto)
```

---

## 🎯 Estrategia Recomendada para el Proyecto

### Bandas a Descargar
```python
REQUIRED_BANDS = {
    # Bandas espectrales para MNDWI
    'SR_B3',        # Green (0.53-0.59 µm) - Landsat 8/9
    'SR_B2',        # Green (0.52-0.60 µm) - Landsat 5/7
    'SR_B6',        # SWIR1 (1.57-1.65 µm) - Landsat 8/9
    'SR_B5',        # SWIR1 (1.55-1.75 µm) - Landsat 5/7
    
    # Bandas de calidad (QA)
    'QA_PIXEL',     # Máscara de nubes/sombras (ESENCIAL)
    'QA_RADSAT',    # Saturación radiométrica (RECOMENDADO)
    'QA_AEROSOL',   # Calidad atmosférica (OPCIONAL)
    
    # Metadatos
    'MTL'           # Metadata text file (opcional, útil para trazabilidad)
}
```

### Pipeline de Enmascaramiento (Capa Silver)
```python
def apply_cloud_mask(green_band, swir_band, qa_pixel, qa_radsat):
    """
    Aplica máscara de calidad para cálculo de MNDWI
    
    Returns:
        numpy.ndarray: Máscara booleana (True = pixel válido)
    """
    # 1. Máscara de nubes/sombras/fill (QA_PIXEL)
    cloud_mask = (qa_pixel & 0b0000000000011011) == 0
    
    # 2. Máscara de saturación para B3 y B6 (QA_RADSAT)
    # Bits: 2 (B3) y 5 (B6) para Landsat 8/9
    #       1 (B2) y 4 (B5) para Landsat 5/7
    saturation_mask = (qa_radsat & 0b0000000000100100) == 0  # L8/9
    
    # 3. Máscara combinada
    valid_pixels = cloud_mask & saturation_mask
    
    return valid_pixels
```

---

## 📚 Referencias Oficiales

- **USGS Landsat Collection 2 QA Tools:**  
  https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands

- **Bit Unpacking Python Example:**  
  https://github.com/USGS-EROS/espa-product-formatter/blob/master/scripts/qa_pixel_unpacking.py

- **Landsat 8-9 Data Users Handbook:**  
  https://www.usgs.gov/media/files/landsat-8-9-olitirs-collection-2-level-2-data-format-control-book

---

## ✅ Decisión para el Proyecto Lago de Valencia

**Bandas QA a usar:**
1. ✅ **QA_PIXEL** → Enmascarar nubes, sombras, fill, dilated clouds
2. ✅ **QA_RADSAT** → Verificar que B3 y B6 no estén saturados
3. ⚠️ **QA_AEROSOL** → Opcional, evaluar después de resultados iniciales

**Bits a enmascarar en QA_PIXEL:**
- Bit 0 (Fill)
- Bit 1 (Dilated Cloud)
- Bit 3 (Cloud)
- Bit 4 (Cloud Shadow)

**Máscara binaria:** `0b0000000000011011` = `0x001B` = `27` (decimal)
