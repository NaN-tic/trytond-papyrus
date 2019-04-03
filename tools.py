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
    out = out.decode('utf-8')
    out = [x for x in out.splitlines() if x.startswith('Pages:')]
    if not out:
        return 0
    out = out[0]
    return int(out.split(':')[-1].strip())

def page_image(path, page):
    if not os.path.exists(path):
        return
    _, jpg_path = tempfile.mkstemp(suffix='.jpg')

    filename = path
    filename += '[%d]' % ((page or 1) - 1)

    # Adding '[0]' to source filename in convert, extracts only the first
    # page of the PDF file
    subprocess.call(['convert', '-quality', '90', '-density', '200x200',
            '-background', 'white', '-alpha', 'remove', filename,
            jpg_path])
    with open(jpg_path, 'rb') as f:
        res = f.read()
    os.unlink(jpg_path)
    return res

def pdftotext(filename):
    if not filename:
        return
    if get_type(filename) != 'PDF':
        return
    out, err = subprocess.Popen(['/usr/bin/pdftotext', '-layout', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE).communicate()
    out = out.decode('utf8')
    return out

def pdftoboxes(filename, Box):
    if not filename:
        return
    if get_type(filename) != 'PDF':
        return
    out, err = subprocess.Popen(['/usr/bin/pdftotext', '-bbox', '-enc',
            'UTF-8', filename, '-'], stdout=subprocess.PIPE).communicate()
    out = out.decode('utf8')

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

def tesseract(filename):
    if not filename:
        return
    if get_type(filename) == 'PDF':
        # TODO: Tesseract on PDF files not supported yet
        return
    content, err = subprocess.Popen(['tesseract', '-l', 'cat', filename,
            'stdout'], stdout=subprocess.PIPE).communicate()
    content = content.decode('utf8')
    return content

def get_type(filename):
    content = subprocess.check_output(['identify', '-format', '"%m"', filename])
    content = content.decode('utf-8').replace('"', '')
    return content

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
            '--milliseconds=10000', filename],
        stdout=subprocess.PIPE).communicate()

    # Each datamatrix is a line of the output
    nextText = False
    box = None
    lines = content.splitlines()
    boxes = []
    extra = {}

    for x in range(len(lines)):
        line = lines[x].decode("utf-8")
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
