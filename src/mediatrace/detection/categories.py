"""File categories and the extension -> (MIME type, category) knowledge base.

The tables are written as compact text blocks: each line lists one or more
extensions followed by the MIME type they share. The first extension registered
for a MIME type becomes its canonical extension.
"""

from __future__ import annotations

from enum import StrEnum


class Category(StrEnum):
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    DOCUMENT = "document"
    SPREADSHEET = "spreadsheet"
    PRESENTATION = "presentation"
    EBOOK = "ebook"
    ARCHIVE = "archive"
    CODE = "code"
    DATA = "data"
    DATABASE = "database"
    FONT = "font"
    EXECUTABLE = "executable"
    DISK_IMAGE = "disk_image"
    DESIGN = "design"
    MODEL_3D = "model_3d"
    SUBTITLE = "subtitle"
    TEXT = "text"
    OTHER = "other"

    @property
    def folder(self) -> str:
        """Human-friendly folder name used by the default organizer rules."""
        return CATEGORY_FOLDERS[self]

    @classmethod
    def parse(cls, value: Category | str) -> Category:
        """Parse a category from its value, name, or folder name (``"images"``, ``"Disk Images"``)."""
        if isinstance(value, Category):
            return value
        key = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        for member in cls:
            if key in (member.value, member.folder.lower().replace(" ", "_")):
                return member
        if key.endswith("s") and key[:-1] in cls._value2member_map_:
            return cls(key[:-1])
        raise ValueError(f"unknown category: {value!r}")


CATEGORY_FOLDERS: dict[Category, str] = {
    Category.IMAGE: "Images",
    Category.VIDEO: "Videos",
    Category.AUDIO: "Audio",
    Category.DOCUMENT: "Documents",
    Category.SPREADSHEET: "Spreadsheets",
    Category.PRESENTATION: "Presentations",
    Category.EBOOK: "Ebooks",
    Category.ARCHIVE: "Archives",
    Category.CODE: "Code",
    Category.DATA: "Data",
    Category.DATABASE: "Databases",
    Category.FONT: "Fonts",
    Category.EXECUTABLE: "Programs",
    Category.DISK_IMAGE: "Disk Images",
    Category.DESIGN: "Design",
    Category.MODEL_3D: "3D Models",
    Category.SUBTITLE: "Subtitles",
    Category.TEXT: "Text",
    Category.OTHER: "Other",
}

#: extension (lowercase, no dot) -> (mime type, category)
EXTENSIONS: dict[str, tuple[str, Category]] = {}
#: mime type -> canonical extension
MIME_TO_EXT: dict[str, str] = {}


def _register(category: Category, table: str) -> None:
    for line in table.strip().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        *exts, mime = line.split()
        for ext in exts:
            EXTENSIONS.setdefault(ext, (mime, category))
        MIME_TO_EXT.setdefault(mime, exts[0])


_register(Category.IMAGE, """
    jpg jpeg jpe jfif jif       image/jpeg
    png                         image/png
    apng                        image/apng
    gif                         image/gif
    webp                        image/webp
    bmp dib                     image/bmp
    tif tiff                    image/tiff
    ico                         image/vnd.microsoft.icon
    cur                         image/x-icon
    heic                        image/heic
    heif hif                    image/heif
    avif                        image/avif
    jxl                         image/jxl
    jp2 j2k jpf jpx             image/jp2
    svg svgz                    image/svg+xml
    tga                         image/x-tga
    pcx                         image/x-pcx
    ppm                         image/x-portable-pixmap
    pgm                         image/x-portable-graymap
    pbm                         image/x-portable-bitmap
    pnm                         image/x-portable-anymap
    qoi                         image/qoi
    exr                         image/x-exr
    hdr                         image/vnd.radiance
    dds                         image/vnd-ms.dds
    dng                         image/x-adobe-dng
    cr2                         image/x-canon-cr2
    cr3                         image/x-canon-cr3
    crw                         image/x-canon-crw
    nef nrw                     image/x-nikon-nef
    arw srf sr2                 image/x-sony-arw
    orf                         image/x-olympus-orf
    rw2                         image/x-panasonic-rw2
    raf                         image/x-fuji-raf
    pef                         image/x-pentax-pef
    srw                         image/x-samsung-srw
    3fr erf kdc mos iiq x3f     image/x-raw
""")

_register(Category.VIDEO, """
    mp4 mp4v mpg4               video/mp4
    m4v                         video/x-m4v
    mov qt                      video/quicktime
    mkv mk3d                    video/x-matroska
    webm                        video/webm
    avi divx                    video/x-msvideo
    wmv                         video/x-ms-wmv
    asf                         video/x-ms-asf
    flv                         video/x-flv
    f4v                         video/x-f4v
    3gp                         video/3gpp
    3g2                         video/3gpp2
    mpg mpeg mpe m1v m2v        video/mpeg
    vob                         video/dvd
    ts m2t                      video/mp2t
    m2ts mts                    video/mp2t
    ogv                         video/ogg
    rm rmvb                     application/vnd.rn-realmedia
    dv                          video/x-dv
    mxf                         application/mxf
    y4m                         video/x-yuv4mpeg
""")

_register(Category.AUDIO, """
    mp3 mpga                    audio/mpeg
    mp2                         audio/mpeg
    m4a m4b m4p m4r             audio/mp4
    aac adts                    audio/aac
    wav wave                    audio/wav
    flac                        audio/flac
    ogg oga                     audio/ogg
    opus                        audio/opus
    spx                         audio/speex
    wma                         audio/x-ms-wma
    aiff aif aifc               audio/aiff
    ape                         audio/x-ape
    wv                          audio/x-wavpack
    mid midi kar rmi            audio/midi
    amr                         audio/amr
    ac3                         audio/ac3
    dts                         audio/vnd.dts
    mka                         audio/x-matroska
    weba                        audio/webm
    au snd                      audio/basic
    caf                         audio/x-caf
    dsf                         audio/x-dsf
    mod xm it s3m               audio/x-mod
""")

_register(Category.DOCUMENT, """
    pdf                         application/pdf
    doc dot                     application/msword
    docx                        application/vnd.openxmlformats-officedocument.wordprocessingml.document
    docm                        application/vnd.ms-word.document.macroenabled.12
    dotx                        application/vnd.openxmlformats-officedocument.wordprocessingml.template
    dotm                        application/vnd.ms-word.template.macroenabled.12
    odt                         application/vnd.oasis.opendocument.text
    ott                         application/vnd.oasis.opendocument.text-template
    rtf                         application/rtf
    pages                       application/vnd.apple.pages
    wpd                         application/vnd.wordperfect
    wps                         application/vnd.ms-works
    xps oxps                    application/oxps
    md markdown mdown mkd       text/markdown
    rst                         text/x-rst
    tex ltx                     application/x-tex
    org                         text/x-org
    adoc asciidoc               text/asciidoc
    eml                         message/rfc822
    msg                         application/vnd.ms-outlook
    one                         application/onenote
    ps                          application/postscript
    vsd                         application/vnd.visio
    vsdx                        application/vnd.ms-visio.drawing
""")

_register(Category.SPREADSHEET, """
    xls xlt xla                 application/vnd.ms-excel
    xlsx                        application/vnd.openxmlformats-officedocument.spreadsheetml.sheet
    xlsm                        application/vnd.ms-excel.sheet.macroenabled.12
    xltx                        application/vnd.openxmlformats-officedocument.spreadsheetml.template
    xltm                        application/vnd.ms-excel.template.macroenabled.12
    xlsb                        application/vnd.ms-excel.sheet.binary.macroenabled.12
    ods                         application/vnd.oasis.opendocument.spreadsheet
    ots                         application/vnd.oasis.opendocument.spreadsheet-template
    csv                         text/csv
    tsv tab                     text/tab-separated-values
    numbers                     application/vnd.apple.numbers
""")

_register(Category.PRESENTATION, """
    ppt pot pps                 application/vnd.ms-powerpoint
    pptx                        application/vnd.openxmlformats-officedocument.presentationml.presentation
    pptm                        application/vnd.ms-powerpoint.presentation.macroenabled.12
    potx                        application/vnd.openxmlformats-officedocument.presentationml.template
    ppsx                        application/vnd.openxmlformats-officedocument.presentationml.slideshow
    odp                         application/vnd.oasis.opendocument.presentation
    otp                         application/vnd.oasis.opendocument.presentation-template
    key                         application/vnd.apple.keynote
""")

_register(Category.EBOOK, """
    epub                        application/epub+zip
    mobi prc                    application/x-mobipocket-ebook
    azw azw3 kfx                application/vnd.amazon.ebook
    fb2                         application/x-fictionbook+xml
    cbz                         application/vnd.comicbook+zip
    cbr                         application/vnd.comicbook-rar
    cb7                         application/x-cb7
    cbt                         application/x-cbt
    djvu djv                    image/vnd.djvu
    ibooks                      application/x-ibooks+zip
    lit                         application/x-ms-reader
""")

_register(Category.ARCHIVE, """
    zip zipx                    application/zip
    rar                         application/vnd.rar
    7z                          application/x-7z-compressed
    tar                         application/x-tar
    gz gzip tgz tar.gz          application/gzip
    bz2 tbz2 tbz tar.bz2        application/x-bzip2
    xz txz tar.xz               application/x-xz
    zst tzst tar.zst            application/zstd
    lz4 tar.lz4                 application/x-lz4
    lzma                        application/x-lzma
    z                           application/x-compress
    cab                         application/vnd.ms-cab-compressed
    arj                         application/x-arj
    lzh lha                     application/x-lzh-compressed
    cpio                        application/x-cpio
    jar war ear                 application/java-archive
    whl                         application/x-wheel+zip
    nupkg                       application/x-nupkg
    crx                         application/x-chrome-extension
    xpi                         application/x-xpinstall
""")

_register(Category.CODE, """
    py pyw pyi                  text/x-python
    pyx pxd                     text/x-cython
    ipynb                       application/x-ipynb+json
    js mjs cjs                  text/javascript
    jsx                         text/jsx
    tsx cts                     text/tsx
    html htm                    text/html
    xhtml                       application/xhtml+xml
    css                         text/css
    scss sass                   text/x-scss
    less                        text/x-less
    java                        text/x-java
    kt kts                      text/x-kotlin
    scala sc                    text/x-scala
    groovy gradle               text/x-groovy
    c h                         text/x-c
    cpp cc cxx hpp hh hxx ino   text/x-c++
    cs csx                      text/x-csharp
    go                          text/x-go
    rs                          text/x-rust
    rb erb                      text/x-ruby
    php phtml                   application/x-httpd-php
    pl pm                       text/x-perl
    lua                         text/x-lua
    r rmd                       text/x-r
    swift                       text/x-swift
    m mm                        text/x-objcsrc
    dart                        text/x-dart
    sh bash zsh ksh fish        application/x-sh
    ps1 psm1 psd1               text/x-powershell
    bat cmd                     application/x-bat
    vb vbs bas                  text/x-vb
    sql                         application/sql
    hs lhs                      text/x-haskell
    ex exs                      text/x-elixir
    erl hrl                     text/x-erlang
    clj cljs cljc edn           text/x-clojure
    elm                         text/x-elm
    jl                          text/x-julia
    nim                         text/x-nim
    zig                         text/x-zig
    vue                         text/x-vue
    svelte                      text/x-svelte
    astro                       text/x-astro
    asm s                       text/x-asm
    f f90 f95 for               text/x-fortran
    pas pp                      text/x-pascal
    ml mli                      text/x-ocaml
    fs fsx fsi                  text/x-fsharp
    sol                         text/x-solidity
    proto                       text/x-protobuf
    graphql gql                 application/graphql
    tf tfvars hcl               text/x-hcl
    cmake                       text/x-cmake
    mk mak                      text/x-makefile
""")

_register(Category.DATA, """
    json                        application/json
    jsonl ndjson                application/x-ndjson
    geojson                     application/geo+json
    xml xsd xsl xslt            application/xml
    yaml yml                    application/yaml
    toml                        application/toml
    ini cfg conf                text/x-ini
    env properties              text/x-properties
    plist                       application/x-plist
    parquet                     application/vnd.apache.parquet
    avro                        application/avro
    orc                         application/x-orc
    feather arrow               application/vnd.apache.arrow.file
    pkl pickle                  application/x-pickle
    npy                         application/x-npy
    npz                         application/x-npz
    h5 hdf5 hdf                 application/x-hdf5
    mat                         application/x-matlab-data
    rss                         application/rss+xml
    atom                        application/atom+xml
    gpx                         application/gpx+xml
    kml                         application/vnd.google-earth.kml+xml
    kmz                         application/vnd.google-earth.kmz
    vcf vcard                   text/vcard
    ics ical                    text/calendar
    pem                         application/x-pem-file
    crt cer der                 application/x-x509-ca-cert
    torrent                     application/x-bittorrent
""")

_register(Category.DATABASE, """
    sqlite sqlite3 db db3 s3db sl3  application/vnd.sqlite3
    mdb                         application/x-msaccess
    accdb                       application/msaccess
    dbf                         application/dbf
    kdbx                        application/x-keepass2
    gpkg                        application/geopackage+sqlite3
    mbtiles                     application/x-mbtiles
    realm                       application/x-realm
""")

_register(Category.FONT, """
    ttf                         font/ttf
    otf                         font/otf
    woff                        font/woff
    woff2                       font/woff2
    ttc                         font/collection
    eot                         application/vnd.ms-fontobject
    fon fnt                     application/x-font-bitmap
    pfb pfm                     application/x-font-type1
""")

_register(Category.EXECUTABLE, """
    exe scr                     application/vnd.microsoft.portable-executable
    dll ocx cpl drv sys efi pyd application/x-msdownload
    msi msp                     application/x-msi
    msix appx appxbundle msixbundle  application/msix
    com                         application/x-msdos-program
    elf                         application/x-executable
    so                          application/x-sharedlib
    o ko                        application/x-object
    dylib                       application/x-mach-binary
    apk xapk                    application/vnd.android.package-archive
    aab                         application/x-authorware-bin
    ipa                         application/x-ios-app
    dex                         application/x-dex
    class                       application/java-vm
    wasm                        application/wasm
    pyc pyo                     application/x-python-code
    deb                         application/vnd.debian.binary-package
    rpm                         application/x-rpm
    pkg mpkg                    application/x-xar
    appimage                    application/vnd.appimage
    run                         application/x-makeself
""")

_register(Category.DISK_IMAGE, """
    iso                         application/x-iso9660-image
    img                         application/x-raw-disk-image
    dmg                         application/x-apple-diskimage
    vhd                         application/x-vhd
    vhdx                        application/x-vhdx
    vmdk                        application/x-vmdk
    qcow2 qcow                  application/x-qemu-disk
    vdi                         application/x-virtualbox-vdi
    wim swm esd                 application/x-ms-wim
    cue                         application/x-cue
    nrg                         application/x-nrg
    toast                       application/x-toast
""")

_register(Category.DESIGN, """
    psd                         image/vnd.adobe.photoshop
    psb                         application/x-photoshop-large
    ai                          application/illustrator
    eps epsf                    application/eps
    indd                        application/x-indesign
    xd                          application/vnd.adobe.xd
    sketch                      application/x-sketch
    fig                         application/x-figma
    xcf                         image/x-xcf
    afdesign afphoto afpub      application/x-affinity
    cdr                         application/vnd.corel-draw
    kra                         application/x-krita
    ora                         image/openraster
    procreate                   application/x-procreate
    drawio                      application/vnd.jgraph.mxfile
    dwg                         image/vnd.dwg
    dxf                         image/vnd.dxf
""")

_register(Category.MODEL_3D, """
    stl                         model/stl
    obj                         model/obj
    fbx                         application/vnd.autodesk.fbx
    glb                         model/gltf-binary
    gltf                        model/gltf+json
    3mf                         model/3mf
    blend                       application/x-blender
    dae                         model/vnd.collada+xml
    ply                         model/x-ply
    usdz                        model/vnd.usdz+zip
    usd usda usdc               model/vnd.usd
    3ds                         application/x-3ds
    max                         application/x-3dsmax
    c4d                         application/x-cinema4d
    skp                         application/vnd.sketchup.skp
    step stp                    model/step
    iges igs                    model/iges
""")

_register(Category.SUBTITLE, """
    srt                         application/x-subrip
    vtt                         text/vtt
    ass ssa                     text/x-ssa
    sub                         text/x-microdvd
    sbv                         text/x-sbv
    ttml dfxp                   application/ttml+xml
    lrc                         text/x-lrc
""")

_register(Category.TEXT, """
    txt text                    text/plain
    log                         text/x-log
    nfo diz                     text/x-nfo
""")

_register(Category.OTHER, """
    lnk                         application/x-ms-shortcut
    url                         application/x-url
    webloc                      application/x-webloc
    desktop                     application/x-desktop
    cfb                         application/x-ole-storage
    bin dat                     application/octet-stream
""")

#: Extensions whose meaning differs when the content is text rather than binary.
#: ``.ts`` is MPEG transport stream video *or* TypeScript source.
TEXT_OVERRIDES: dict[str, tuple[str, Category]] = {
    "ts": ("text/x-typescript", Category.CODE),
    "mts": ("text/x-typescript", Category.CODE),
}

#: Data formats that are binary even though they live in the DATA category.
_BINARY_DATA = frozenset(
    "parquet avro orc feather arrow pkl pickle npy npz h5 hdf5 hdf mat kmz der torrent".split()
)
#: Text-based formats that live in non-text categories.
_TEXTUAL_EXTRA = frozenset(
    "csv tsv tab md markdown mdown mkd rst tex ltx org adoc asciidoc eml svg ps eps epsf "
    "fb2 obj stl gltf dae ply usda drawio dxf cue html htm xhtml".split()
)

MEDIA_CATEGORIES = frozenset({Category.IMAGE, Category.VIDEO, Category.AUDIO})


def lookup_extension(ext: str) -> tuple[str, Category] | None:
    """Return ``(mime, category)`` for an extension (with or without a leading dot)."""
    return EXTENSIONS.get(ext.lower().lstrip("."))


def is_textual_extension(ext: str) -> bool:
    """True if files with this extension are expected to contain text."""
    ext = ext.lower().lstrip(".")
    if ext in TEXT_OVERRIDES or ext in _TEXTUAL_EXTRA:
        return True
    entry = EXTENSIONS.get(ext)
    if entry is None:
        return False
    mime, category = entry
    if category in (Category.TEXT, Category.CODE, Category.SUBTITLE):
        return True
    if category is Category.DATA:
        return ext not in _BINARY_DATA
    return mime.startswith("text/")


def category_from_mime(mime: str) -> Category:
    """Best-effort category for a MIME type that is not in the extension table."""
    ext = MIME_TO_EXT.get(mime)
    if ext:
        return EXTENSIONS[ext][1]
    major = mime.split("/", 1)[0]
    return {
        "image": Category.IMAGE,
        "video": Category.VIDEO,
        "audio": Category.AUDIO,
        "font": Category.FONT,
        "text": Category.TEXT,
        "model": Category.MODEL_3D,
    }.get(major, Category.OTHER)
