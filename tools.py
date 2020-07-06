# This file is part papyrus module for Tryton.
# The COPYRIGHT file at the top level of this repository contains
# the full copyright notices and license terms.
import subprocess
import json
import tempfile
import os
from xml.etree import ElementTree

IDENTIFY_FORMATS = ['PNG', 'JPG', 'JPEG', 'GIF', 'PDF']

def page_count(path):
    # TODO: Ensure path is a PDF. If its not a PDF return 1?
    if not os.path.exists(path):
        # When the file has not yet been created (maybe because FileDataManager
        # has not been commited yet, the file may not exists) better quit
        # quitely
        return
    out, err = subprocess.Popen(['/usr/bin/pdfinfo', path],
        stdout=subprocess.PIPE).communicate()
    out = out.decode('utf-8', errors='replace')
    out = [x for x in out.splitlines() if x.startswith('Pages:')]
    if not out:
        return 0
    out = out[0]
    return int(out.split(':')[-1].strip())

def page_image(path, page, unlink=True):
    if not os.path.exists(path):
        return
    _, jpg_path = tempfile.mkstemp(suffix='.jpg')
    try:
        filename = path
        filename += '[%d]' % ((page or 1) - 1)

        # Adding '[0]' to source filename in convert, extracts only the first
        # page of the PDF file
        subprocess.call(['convert', '-quality', '90', '-density', '100x100',
                '-background', 'white', '-alpha', 'remove', filename,
                jpg_path])
        if unlink:
            with open(jpg_path, 'rb') as f:
                res = f.read()
            return res
        else:
            return jpg_path
    finally:
        if unlink:
            os.unlink(jpg_path)

def pdftotext(filename):
    if not filename:
        return
    if get_type(filename) != 'PDF':
        return
    out, err = subprocess.Popen(['/usr/bin/pdftotext', '-layout', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE).communicate()
    out = out.decode('utf8', errors='replace')
    return out

def pdftoboxes(filename, Box):
    if not filename:
        return []
    if get_type(filename) != 'PDF':
        return []
    out, err = subprocess.Popen(['/usr/bin/pdftotext', '-bbox', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE).communicate()
    out = out.decode('utf8', 'replace')

    root = ElementTree.fromstring(out)
    body = root[1]
    assert body.tag.endswith('body'), body.tag
    doc = body[0]
    assert doc.tag.endswith('doc'), doc.tag

    boxes = []
    for page in doc:
        for word in page:
            box = Box()
            box.type = 'text'
            box.x0 = word.attrib['xMin']
            box.x1 = word.attrib['xMax']
            box.y0 = word.attrib['yMin']
            box.y1 = word.attrib['yMax']
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
                boxes += jpg_boxes
        return '\n'.join(content), boxes

    _, pdf_path = tempfile.mkstemp(suffix='.pdf')
    # Remove extension from filename because tesseract adds it again
    tess_pdf_path, _ = os.path.splitext(pdf_path)
    try:
        content, err = subprocess.Popen(['tesseract', filename, 'stdout'],
            stdout=subprocess.PIPE).communicate()
        content = content.decode('utf8', errors='replace')

        _, err = subprocess.Popen(['tesseract', filename, tess_pdf_path,
                'pdf'], stdout=subprocess.PIPE).communicate()
        boxes = pdftoboxes(pdf_path, Box)
        return content, boxes
    finally:
        os.unlink(pdf_path)
        pass

def get_type(filename):
    try:
        content = subprocess.check_output(['identify', '-format', '"%m"', filename])
    except subprocess.CalledProcessError:
        return
    content = content.decode('utf-8', errors='replace').replace('"', '')
    # Return only the first 3 characters as identify may return type for each
    # page of the document
    return content[:3]

def datamatrix(filename, Box):
    """
    Parse dmtxread output which looks like this:
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
    Wikipedia, the free encyclopedia
    """
    if not filename:
        return

    content, err = subprocess.Popen(['dmtxread', '--newline', '--verbose',
            '--milliseconds=10000', filename], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE).communicate()

    if err:
        content = err + content

    # Each datamatrix is a line of the output
    nextText = False
    box = None
    lines = content.splitlines()
    boxes = []
    extra = {}
    for x in range(len(lines)):
        line = lines[x].decode('utf-8', errors='replace')
        if not box and line == ('-' * 50):
            continue

        if not box:
            box = Box()
            box.type = 'barcode'
            boxes.append( box )

        if nextText:
            box.text = line
            if extra:
                box.extra = json.dumps(extra)
            nextText = False
            box = None
            continue
        if line == ('-' * 50):
            nextText = True
            continue

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
