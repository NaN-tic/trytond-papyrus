# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import subprocess
import json
import tempfile
import os
import shutil
from lxml import etree

IDENTIFY_FORMATS = ['PNG', 'JPG', 'JPEG', 'GIF', 'PDF']

def page_count(path):
    # TODO: Ensure path is a PDF. If its not a PDF return 1?
    if not os.path.exists(path):
        # When the file has not yet been created (maybe because FileDataManager
        # has not been commited yet, the file may not exists) better quit
        # quitely
        return
    process = subprocess.Popen(['/usr/bin/pdfinfo', path],
        stdout=subprocess.PIPE, encoding='utf-8', errors='replace')
    out, err = process.communicate()
    out = [x for x in out.splitlines() if x.startswith('Pages:')]
    if not out:
        return 0
    out = out[0]
    return int(out.split(':')[-1].strip())

def page_image(path, page, unlink=True, suffix='.jpg', alpha=False, quality=90,
        density=100, height=None, width=None):
    if not os.path.exists(path):
        return
    _, image_path = tempfile.mkstemp(suffix=suffix)
    try:
        filename = path
        filename += '[%d]' % ((page or 1) - 1)

        # Adding '[0]' to source filename in convert, extracts only the first
        # page of the PDF file
        command = ['convert', '-quality', str(quality),
            '-density', '%sx%s' % (density, density)]
        if not alpha:
            command += ['-background', 'white', '-alpha', 'remove']
        if height or width:
            command += ['-resize', '%sx%s' % (width or '', height or '')]
        command += [filename, image_path]
        subprocess.call(command)
        if unlink:
            with open(image_path, 'rb') as f:
                res = f.read()
            return res
        else:
            return image_path
    finally:
        if unlink:
            os.unlink(image_path)

def pdftotext(filename):
    if not filename:
        return
    if get_type(filename) != 'PDF':
        return
    process = subprocess.Popen(['/usr/bin/pdftotext', '-layout', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE, encoding='utf-8',
        errors='replace')
    out, err = process.communicate()
    return out

def pdftoboxes(filename, Box):
    if not filename:
        return []
    if get_type(filename) != 'PDF':
        return []
    process = subprocess.Popen(['/usr/bin/pdftotext', '-bbox', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE, encoding='utf-8',
        errors='replace')
    out, _ = process.communicate()

    parser = etree.XMLParser(recover=True)
    root = etree.fromstring(out, parser=parser)
    body = root[1]
    assert body.tag.endswith('body'), body.tag
    doc = body[0]
    assert doc.tag.endswith('doc'), doc.tag

    boxes = []
    current_page = 0
    for page in doc:
        current_page += 1
        for word in page:
            box = Box()
            if hasattr(Box, 'page'):
                box.page = current_page
            box.type = 'text'
            box.x0 = float(word.attrib['xMin'])
            box.x1 = float(word.attrib['xMax'])
            box.y0 = float(word.attrib['yMin'])
            box.y1 = float(word.attrib['yMax'])
            box.text = word.text

            boxes.append(box)
    return boxes

def tesseract(filename, Box):
    if not filename:
        return '', []
    if get_type(filename) == 'PDF':
        count = page_count(filename)
        if not count:
            return '', []

        content = []
        boxes = []
        for page in range(count):
            jpg_path = page_image(filename, page, unlink=False)
            try:
                jpg_content, jpg_boxes = tesseract(jpg_path, Box)
            finally:
                os.unlink(jpg_path)
                pass
            if jpg_content:
                content.append(jpg_content)
            if jpg_boxes:
                if hasattr(Box, 'page'):
                    for box in jpg_boxes:
                        box.page = page + 1
                boxes += jpg_boxes
        return '\n'.join(content), boxes

    _, pdf_path = tempfile.mkstemp(suffix='.pdf')
    # Remove extension from filename because tesseract adds it again
    tess_pdf_path, _ = os.path.splitext(pdf_path)
    try:
        # Execute tesseract twice:

        # In the first one we get plain text
        process = subprocess.Popen(['tesseract', filename, 'stdout'],
            stdout=subprocess.PIPE, encoding='utf-8', errors='replace')
        content, _ = process.communicate()

        # In the second one we get text boxes
        process = subprocess.Popen(['tesseract', filename, tess_pdf_path,
                'pdf'], stdout=subprocess.PIPE, encoding='utf-8',
            errors='replace')
        process.communicate()
        boxes = pdftoboxes(pdf_path, Box)
        return content, boxes
    finally:
        os.unlink(pdf_path)
        pass

def get_type(filename):
    try:
        content = subprocess.check_output(['identify', '-format', '"%m"',
                filename], encoding='utf-8', errors='replace')
    except subprocess.CalledProcessError:
        return
    content = content.replace('"', '')
    # Return only the first 3 characters as identify may return type for each
    # page of the document
    return content[:3]

def datamatrix(filename, Box):
    """
    Parse dmtxread output which looks like this:

    Stdout (each DataMatrix is a line of the output):
    Wikipedia, the free encyclopedia

    Stderr (each DataMatrix is enclosed between '-' * 50 separators):
    --------------------------------------------------
           Matrix Size: 22 x 22
        Data Codewords: 26 (capacity 30)
       Error Codewords: 20
          Data Regions: 1 x 1
    Interleaved Blocks: 1
        Rotation Angle: 0
              Corner 0: (1500.0, 508.0)
              Corner 1: (1701.0, 508.0)
              Corner 2: (1701.0, 307.0)
              Corner 3: (1500.0, 307.0)
    --------------------------------------------------
    """
    if not filename:
        return

    process = subprocess.Popen(['dmtxread', '--newline', '--verbose',
            '--milliseconds=10000', filename], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding='utf-8', errors='replace')
    stdout, stderr = process.communicate()
    if process.returncode:
        return []

    # Split lines using \n because dmtxread prints some characters that are
    # incorrectly understood as new lines by 'splitlines()'
    stdout = stdout.split('\n')
    stderr = stderr.split('\n')
    boxes = []
    box = None
    extra = {}
    for line in stderr:
        if not line:
            continue

        if not box and line == ('-' * 50):
            continue

        if line == ('-' * 50):
            if extra:
                box.extra = json.dumps(extra)
                extra = {}
            box = None
            continue

        if not box:
            box = Box()
            box.type = 'barcode'
            box.text = stdout[len(boxes)]
            boxes.append( box )

        key, value = line.split(':')
        key = key.strip()
        value = value.strip()
        if key == 'Corner 0':
            value = value.strip('()')
            box.x0, box.y0 = value.split(',')
        elif key == 'Corner 2':
            value = value.strip('()')
            box.x1, box.y1 = value.split(',')
        else:
            extra[key] = value
    return boxes

def soffice_convert(data, from_extension, to_extension, timeout=15):
    temp_dir = tempfile.mkdtemp()
    try:
        input_filename = os.path.join(temp_dir, 'file.%s' % from_extension)
        with open(input_filename, 'wb') as f:
            f.write(data)
        process = subprocess.Popen(['soffice', '--headless', '--nolockcheck',
                '--nodefault', '--norestore', '--convert-to', to_extension,
                '--outdir', temp_dir, input_filename])
        try:
            process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            return
        output_filename = os.path.join(temp_dir, 'file.%s' % to_extension)
        try:
            with open(output_filename, 'rb') as f:
                return f.read()
        except FileNotFoundError:
            return
    finally:
        shutil.rmtree(temp_dir)

