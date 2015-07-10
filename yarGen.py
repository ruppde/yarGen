#!/usr/bin/env python
# -*- coding: iso-8859-1 -*-
# -*- coding: utf-8 -*-
#
# yarGen
# A rule generator for Yara rules
#
# Florian Roth

import os
import sys
import argparse
import re
import traceback
import pickle
import gzip
import operator
import datetime
import time
import scandir
from collections import Counter
from hashlib import sha256
from naiveBayesClassifier import tokenizer
from naiveBayesClassifier.trainer import Trainer
from naiveBayesClassifier.classifier import Classifier

try:
    from lxml import etree
    lxml_available = True
except Exception, e:
    print "[E] lxml not found - disabling PeStudio string check functionality"
    lxml_available = False

from lib import gibDetector

RELEVANT_EXTENSIONS = [ ".asp", ".vbs", ".ps", ".ps1", ".tmp", ".bas", ".bat", ".cmd", ".com", ".cpl",
                         ".crt", ".dll", ".exe", ".hta", ".msc", ".reg", ".html", ".htm", ".txt", ".jpg", ".c",
                         ".scr", ".sys", ".vb", ".vbe", ".vbs", ".wsc", ".wsf", ".wsh", ".ct", ".t", ".input",
                         ".war", ".jsp", ".php", ".asp", ".aspx", ".psd1", ".psm1" ]

def getFiles(dir, notRecursive):
    # Not Recursive
    if notRecursive:
        for filename in os.listdir(dir):
            filePath = os.path.join(dir,filename)
            if os.path.isdir(filePath):
                continue
            yield filePath
    # Recursive
    else:
        for root, directories, files in scandir.walk (dir, followlinks=False):
            for filename in files:
                filePath = os.path.join(root,filename)
                yield filePath


def parseSampleDir(dir, notRecursive=False, generateInfo=False, onlyRelevantExtensions=False):

    # Prepare dictionary
    string_stats = {}
    file_info = {}
    known_sha1sums = []

    for filePath in getFiles(dir, notRecursive):

        try:

            # Get Extension
            extension = os.path.splitext(filePath)[1].lower()
            if not extension in RELEVANT_EXTENSIONS and onlyRelevantExtensions:
                if args.debug:
                    print "[-] EXTENSION %s - Skipping file %s" % ( extension, filePath )
                continue

            # Size Check
            size = 0
            try:
                size = os.stat(filePath).st_size
                if size > ( args.fs * 1024 * 1024 ):
                    if args.debug:
                        print "[-] File is to big - Skipping file %s (use -fs to adjust this behaviour)" % ( filePath )
                    continue
            except Exception, e:
                pass

            # Extract strings from file
            ( strings, sha256sum ) = extractStrings(filePath, generateInfo)

            # Skip if MD5 already known - avoid duplicate files
            if sha256sum in known_sha1sums:
                #if args.debug:
                print "[-] Skipping strings from %s due to MD5 duplicate detection" % filePath
                continue

            # Add md5 value
            if generateInfo:
                known_sha1sums.append(sha256sum)
                file_info[filePath] = {}
                file_info[filePath]["hash"] = sha256sum

            # Magic evaluation
            if not args.nomagic:
                file_info[filePath]["magic"] = getMagic(filePath)
            else:
                file_info[filePath]["magic"] = ""

            # File Size
            file_info[filePath]["size"] = os.stat(filePath).st_size

            # Add stats for basename (needed for inverse rule generation)
            fileName = os.path.basename(filePath)
            folderName = os.path.basename(os.path.dirname(filePath))
            if fileName not in file_info:
                file_info[fileName] = {}
                file_info[fileName]["count"] = 0
                file_info[fileName]["hashes"] = []
                file_info[fileName]["folder_names"] = []
            file_info[fileName]["count"] += 1
            file_info[fileName]["hashes"].append(sha256sum)
            if folderName not in file_info[fileName]["folder_names"]:
                file_info[fileName]["folder_names"].append(folderName)

            # Add strings to statistics
            invalid_count = 0
            for string in strings:
                # String is not already known
                if string not in string_stats:
                    string_stats[string] = {}
                    string_stats[string]["count"] = 0
                    string_stats[string]["files"] = []
                    string_stats[string]["files_basename"] = {}
                # String count
                string_stats[string]["count"] += 1
                # Add file information
                if fileName not in string_stats[string]["files_basename"]:
                    string_stats[string]["files_basename"][fileName] = 0
                string_stats[string]["files_basename"][fileName] += 1
                string_stats[string]["files"].append(filePath)

            if args.debug:
                print "[+] Processed " + filePath + " Size: "+ str(size) +" Strings: "+ str(len(string_stats)) + " ... "

        except Exception, e:
            traceback.print_exc()
            print "[E] ERROR reading file: %s" % filePath

    return string_stats, file_info


def parseGoodDir(dir, notRecursive=False, onlyRelevantExtensions=True):

    # Prepare dictionary
    all_strings = Counter()

    for filePath in getFiles(dir, notRecursive):
        # Get Extension
        extension = os.path.splitext(filePath)[1].lower()
        if extension not in RELEVANT_EXTENSIONS and onlyRelevantExtensions:
            if args.debug:
                print "[-] EXTENSION %s - Skipping file %s" % ( extension, filePath )
            continue

        # Size Check
        size = 0
        try:
            size = os.stat(filePath).st_size
            if size > 3000000:
                continue
        except Exception, e:
            pass

        # Extract strings from file
        ( strings, sha1sum ) = extractStrings(filePath, generateInfo=False)
        # Append to all strings
        all_strings.update(strings)

    # return it as a set (unique strings)
    return all_strings


def extractStrings(filePath, generateInfo):
    # String list
    strings = []
    cleaned_strings	= []
    sha1sum = ""
    # Read file data
    try:
        f = open(filePath, 'rb')
        print "[-] Processing: %s" % filePath
        data = f.read()
        f.close()
        # Generate md5
        if generateInfo:
            sha256sum = sha256(data).hexdigest()

        # Read strings
        strings = re.findall("[\x1f-\x7e]{6,}", data)
        if args.debug:
            print "%s ASCII strings extracted" % len(strings)
            ascii_count = len(strings)
        strings += [str("UTF16LE:%s" % ws.decode("utf-16le")) for ws in re.findall("(?:[\x1f-\x7e][\x00]){6,}", data)]
        if args.debug:
            print "%s ASCII strings extracted" % ( len(strings) - ascii_count )

        # Escape strings
        for string in strings:
            # Check if last bytes have been string and not yet saved to list
            if len(string) > 0:
                string = string.replace('\\','\\\\')
                string = string.replace('"','\\"')
                if string not in cleaned_strings:
                    cleaned_strings.append(string.lstrip(" "))

    except Exception,e:
        if args.debug:
            traceback.print_exc()
        pass

    return cleaned_strings, sha256sum


def sampleStringEvaluation(sample_string_stats, good_strings, file_info):

    # Generate Stats --------------------------------------------------
    print "[+] Generating statistical data ..."
    file_strings = {}
    combinations = {}
    inverse_stats = {}
    max_combi_count = 0

    # Iterate through strings found in malware files
    for string in sample_string_stats:

        # String file basename stats - used in inverse rule generation
        file_basename_stats = {}

        # If string occurs not too often in sample files
        if sample_string_stats[string]["count"] < 10:
            if args.debug:
                # print "String: " +string +" Found in: "+ ", ".join(mal_string_stats[string]["files"])
                pass
            # If string list in file dictionary not yet exists
            for filePath in sample_string_stats[string]["files"]:
                if filePath in file_strings:
                    # Append string
                    file_strings[filePath].append(string)
                else:
                    # Create list and than add the first string to the file
                    file_strings[filePath] = []
                    file_strings[filePath].append(string)

                # INVERSE RULE GENERATION -------------------------------------

                for fileName in sample_string_stats[string]["files_basename"]:
                    string_occurance_count = sample_string_stats[string]["files_basename"][fileName]
                    total_count_basename = file_info[fileName]["count"]
                    # print "string_occurance_count %s - total_count_basename %s" % ( string_occurance_count, total_count_basename )
                    if string_occurance_count == total_count_basename:
                        if fileName not in inverse_stats:
                            inverse_stats[fileName] = []
                        if args.debug:
                            print "Appending %s to %s" % ( string, fileName )
                        inverse_stats[fileName].append(string)

        # SUPER RULE GENERATION -------------------------------------------

        super_rules = []
        if not args.nosuper and not args.inverse:

            # SUPER RULES GENERATOR	- preliminary work
            # If a string occurs more than once in different files
            # print sample_string_stats[string]["count"]
            if sample_string_stats[string]["count"] > 1:
                if args.debug:
                    print "OVERLAP Count: %s\nString: \"%s\"%s" % ( sample_string_stats[string]["count"], string, "\nFILE: ".join(sample_string_stats[string]["files"]) )
                # Create a combination string from the file set that matches to that string
                combi = ":".join(sorted(sample_string_stats[string]["files"]))
                # print "STRING: " + string
                # print "COMBI: " + combi
                # If combination not yet known
                if combi not in combinations:
                    combinations[combi] = {}
                    combinations[combi]["count"] = 1
                    combinations[combi]["strings"] = []
                    combinations[combi]["strings"].append(string)
                    combinations[combi]["files"] = sample_string_stats[string]["files"]
                else:
                    combinations[combi]["count"] += 1
                    combinations[combi]["strings"].append(string)
                # Set the maximum combination count
                if combinations[combi]["count"] > max_combi_count:
                    max_combi_count = combinations[combi]["count"]
                    # print "Max Combi Count set to: %s" % max_combi_count

    print "[+] Generating Super Rules ... (a lot of foo magic)"
    for combi_count in range(max_combi_count, 1, -1):
        for combi in combinations:
            if combi_count == combinations[combi]["count"]:
                #print "Count %s - Combi %s" % ( str(combinations[combi]["count"]), combi )
                # Filter the string set
                #print "BEFORE"
                #print len(combinations[combi]["strings"])
                #print combinations[combi]["strings"]
                string_set = combinations[combi]["strings"]
                combinations[combi]["strings"] = []
                combinations[combi]["strings"] = filterStringSet(string_set)
                #print combinations[combi]["strings"]
                #print "AFTER"
                #print len(combinations[combi]["strings"])
                # Combi String count after filtering
                #print "String count after filtering: %s" % str(len(combinations[combi]["strings"]))

                # If the string set of the combination has a required size
                if len(combinations[combi]["strings"]) >= int(args.rc):
                    # Remove the files in the combi rule from the simple set
                    if args.nosimple:
                        for file in combinations[combi]["files"]:
                            if file in file_strings:
                                del file_strings[file]
                    # Add it as a super rule
                    print "[-] Adding Super Rule with %s strings." % str(len(combinations[combi]["strings"]))
                    #if args.debug:
                    #print "Rule Combi: %s" % combi
                    super_rules.append(combinations[combi])

    # Return all data
    return (file_strings, combinations, super_rules, inverse_stats)


def filterStringSet(string_set):

    # This is the only set we have - even if it's a weak one
    useful_set = []

    # Gibberish Detector (obsolete)
    # gib = gibDetector.GibDetector()

    # Bayes Classificator (new method)
    stringClassifier = Classifier(stringTrainer.data, tokenizer)

    # Local string scores
    localStringScores = {}

    # Local UTF strings
    utfstrings = []

    for string in string_set:

        # Goodware string marker
        goodstring = False
        goodcount = 0

        # Goodware Strings
        if string in good_strings:
            goodstring = True
            goodcount = good_strings[string]
            # print "%s - %s" % ( goodstring, good_strings[string] )
            if args.excludegood:
                continue

        # UTF
        original_string = string
        if string[:8] == "UTF16LE:":
            # print "removed UTF16LE from %s" % string
            string = string[8:]
            utfstrings.append(string)

        # Good string valuation (after the UTF modification)
        if goodstring:
            # Reduce the score by the number of occurence in goodware files
            localStringScores[string] = ( goodcount * -1 ) + 5
        else:
            localStringScores[string] = 0

        # PEStudio String Blacklist Evaluation
        if pestudio_available:
            ( pescore, type ) = getPEStudioScore(string)
            # print string
            # Reset score of goodware files to 5 if blacklisted in PEStudio
            if type != "":
                pestudioMarker[string] = type
                # Modify the PEStudio blacklisted strings with their goodware stats count
                if goodstring:
                    pescore = pescore - ( goodcount / 1000.0 )
                    # print "%s - %s - %s" % (string, pescore, goodcount)
                localStringScores[string] = pescore

        if not goodstring:

            # Gibberish Score (obsolete)
            #score = gib.getScore(string)
            ## score = 1
            ##if score > 10:
            ##    score = 1
            #score = score / 2
            ##if args.debug:
            ##    print "Gibberish %s - %s" % ( str(score), string)
            #localStringScores[string] += score

            # Bayes Classifier
            classification = stringClassifier.classify(string)
            if classification[0][1] == 0 and len(string) > 10:
                # Try to split the string into words and then check again
                modified_string = re.sub(r'[\\\/\-\.\_<>="\']', ' ', string).rstrip(" ").lstrip(" ")
                # print "Checking instead: %s" % modified_string
                classification = stringClassifier.classify(modified_string)

            if args.debug:
                print "[D] Bayes Score: %s %s" % (str(classification), string)
            localStringScores[string] += classification[0][1]

            # Length Score
            length = len(string)
            if length > int(args.l) and length < int(args.s):
                localStringScores[string] += round( len(string) / 8, 2)
            if length >= int(args.s):
                localStringScores[string] += 1

            # In suspicious strings
            #if string in suspicious_strings:
            #    localStringScores[string] += 6

            # Reduction
            if ".." in string:
                localStringScores[string] -= 5
            if "   " in string:
                localStringScores[string] -= 5
            # Packer Strings
            if re.search(r'(WinRAR\\SFX)', string):
                localStringScores[string] -= 4
            # US ASCII char
            if "\x1f" in string:
                localStringScores[string] -= 4

            # Certain strings add-ons ----------------------------------------------
            # Extensions - Drive
            if re.search(r'([A-Za-z]:\\|\.exe|\.pdb|\.scr|\.log|\.cfg|\.txt|\.dat|\.msi|\.com|\.bat|\.dll|\.pdb|\.vbs|\.tmp|\.sys)', string, re.IGNORECASE):
                localStringScores[string] += 4
            # System keywords
            if re.search(r'(cmd.exe|system32|users|Documents and|SystemRoot|Grant|hello|password|process|log|unc)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Protocol Keywords
            if re.search(r'(ftp|irc|smtp|command|GET|POST|Agent|tor2web|HEAD)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Connection keywords
            if re.search(r'(error|http|closed|fail|version)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Browser User Agents
            if re.search(r'(Mozilla|MSIE|Windows NT|Macintosh|Gecko|Opera|User\-Agent)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Temp and Recycler
            if re.search(r'(TEMP|Temporary|Appdata|Recycler)', string, re.IGNORECASE):
                localStringScores[string] += 4
            # malicious keywords - hacktools
            if re.search(r'(scan|sniff|poison|fake|spoof|sweep|dump|flood|inject|forward|scan|vulnerable|credentials|creds|coded|p0c|Content|host)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # network keywords
            if re.search(r'(address|port|listen|remote|local|process|service|mutex|pipe|frame|key|lookup|connection)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Drive
            if re.search(r'([C-Zc-z]:\\)', string, re.IGNORECASE):
                localStringScores[string] += 4
            # IP
            if re.search(r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b', string, re.IGNORECASE): # IP Address
                localStringScores[string] += 5
            # Copyright Owner
            if re.search(r'( by | coded | c0d3d |cr3w\b)', string, re.IGNORECASE):
                localStringScores[string] += 2
            # Extension generic
            if re.search(r'\.[a-zA-Z]{3}\b', string):
                localStringScores[string] += 3
            # All upper case
            if re.search(r'^[A-Z]{6,}$', string):
                localStringScores[string] += 2.5
            # All lower case
            if re.search(r'^[a-z]{6,}$', string):
                localStringScores[string] += 2
            # All lower with space
            if re.search(r'^[a-z\s]{6,}$', string):
                localStringScores[string] += 2
            # All characters
            if re.search(r'^[A-Z][a-z]{5,}$', string):
                localStringScores[string] += 2
            # URL
            if re.search(r'(%[a-z][:\-,;]|\\\\%s|\\\\[A-Z0-9a-z%]+\\[A-Z0-9a-z%]+)', string):
                localStringScores[string] += 3
            # certificates
            if re.search(r'(thawte|trustcenter|signing|class|crl|CA|certificate|assembly)', string, re.IGNORECASE):
                localStringScores[string] -= 4
            # Parameters
            if re.search(r'( \-[a-z]{,2}[\s]?[0-9]?| /[a-z]+[\s]?[\w]*)', string, re.IGNORECASE):
                localStringScores[string] += 4
            # Directory
            if re.search(r'([a-zA-Z]:|^|%)\\[A-Za-z]{4,30}\\', string):
                localStringScores[string] += 4
            # Executable - not in directory
            if re.search(r'^[^\\]+\.(exe|com|scr|bat)$', string, re.IGNORECASE):
                localStringScores[string] += 4
            # Date placeholders
            if re.search(r'(yyyy|hh:mm|dd/mm|mm/dd|%s:%s:)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Placeholders
            if re.search(r'[^A-Za-z](%s|%d|%i|%02d|%04d|%2d|%3s)[^A-Za-z]', string, re.IGNORECASE):
                localStringScores[string] += 3
            # String parts from file system elements
            if re.search(r'(cmd|com|pipe|tmp|temp|recycle|bin|secret|private|AppData|driver|config)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Programming
            if re.search(r'(execute|run|system|shell|root|cimv2|login|exec|stdin|read|process|netuse|script|share)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Credentials
            if re.search(r'(user|pass|login|logon|token|cookie|creds|hash|ticket|NTLM|LMHASH|kerberos|spnego|session|identif|account|login|auth|privilege)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Malware
            if re.search(r'(\.[a-z]/[^/]+\.txt|)', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Variables
            if re.search(r'%[A-Z_]+%', string, re.IGNORECASE):
                localStringScores[string] += 4
            # RATs / Malware
            if re.search(r'(spy|logger|dark|cryptor|RAT\b|eye|comet|evil|xtreme|poison)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Missed user profiles
            if re.search(r'[\\](users|profiles|username|benutzer|Documents and Settings|Utilisateurs|Utenti|Usu�rios)[\\]', string, re.IGNORECASE):
                localStringScores[string] += 3
            # Strings: Words ending with numbers
            if re.search(r'^[A-Z][a-z]+[0-9]+$', string, re.IGNORECASE):
                localStringScores[string] += 1
            # Program Path - not Programs or Windows
            if re.search(r'^[Cc]:\\\\[^PW]', string):
                localStringScores[string] += 3
            # Special strings
            if re.search(r'(\\\\\.\\|kernel|.dll|usage|\\DosDevices\\)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Parameters
            if re.search(r'( \-[a-z] | /[a-z] | \-[a-z]:[a-zA-Z]| \/[a-z]:[a-zA-Z])', string):
                localStringScores[string] += 4
            # File
            if re.search(r'^[a-zA-Z0-9]{3,40}\.[a-zA-Z]{3}', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Comment Line / Output Log
            if re.search(r'^([\*\#]+ |\[[\*\-\+]\] |[\-=]> |\[[A-Za-z]\] )', string):
                localStringScores[string] += 4
            # Output typo / special expression
            if re.search(r'(!.$|!!!$| :)$| ;)$|fucked|[\w]\.\.\.\.$)', string):
                localStringScores[string] += 4
            # Base64
            if re.search(r'^(?:[A-Za-z0-9+/]{4}){30,}(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$', string):
                localStringScores[string] += 4
            # Malicious intent
            if re.search(r'(loader|cmdline|ntlmhas|lmhash|drop|infect|encrypt|exec|elevat|dump|target|victim|override|traverse|mutex|pawnde|exploited|shellcode|injected|spoofed)', string, re.IGNORECASE):
                localStringScores[string] += 5
            # Privileges
            if re.search(r'(administrator|highest|system|debug|dbg|admin|adm|root) privilege', string, re.IGNORECASE):
                localStringScores[string] +=
            # System file/process names
            if re.search(r'(LSASS|SAM|lsass.exe|cmd.exe|LSASRV.DLL)', string):
                localStringScores[string] += 4

            # BASE64 --------------------------------------------------------------
            try:
                if len(string) > 8:
                    # Try different ways - fuzz string
                    for m_string in ( string, string[1:], string[1:] + "=", string + "=", string + "==" ):
                        if isBase64(m_string):
                            decoded_string = m_string.decode('base64')
                            # print decoded_string
                            if isAsciiString(decoded_string, padding_allowed=True):
                                # print "match"
                                localStringScores[string] += 6
                                base64strings[string] = decoded_string
            except Exception, e:
                pass

            # Reversed String -----------------------------------------------------
            if string[::-1] in good_strings:
                localStringScores[string] += 10
                reversedStrings[string] = string[::-1]

            # Certain string reduce	-----------------------------------------------
            if re.search(r'(rundll32\.exe$|kernel\.dll$)', string, re.IGNORECASE):
                localStringScores[string] -= 4

        # Set the global string score
        stringScores[original_string] = localStringScores[string]

        if args.debug:
            if string in utfstrings:
                is_utf = True
            else:
                is_utf = False
            # print "SCORE: %s\tUTF: %s\tSTRING: %s" % ( localStringScores[string], is_utf, string )

    sorted_set = sorted(localStringScores.iteritems(), key=operator.itemgetter(1), reverse=True)

    # Only the top X strings
    c = 0
    result_set = []
    for string in sorted_set:

        # Skip the one with a score lower than -z X
        if not args.noscorefilter and not args.inverse:
            if string[1] < int(args.z):
                continue

        if string[0] in utfstrings:
            result_set.append("UTF16LE:%s" % string[0])
        else:
            result_set.append(string[0])

        c += 1
        if c > int(args.rc):
            break

    if args.debug:
        print "RESULT SET:"
        print result_set

    # return the filtered set
    return result_set


def generateGeneralCondition(file_info):

    condition = ""

    # Different Magic Headers and File Sizes
    magic_headers = []
    file_sizes = []

    try:
        for filePath in file_info:
            # Short file name info used for inverse generation has no magic/size fields
            if "magic" not in file_info[filePath]:
                continue
            magic = file_info[filePath]["magic"]
            size = file_info[filePath]["size"]
            if magic not in magic_headers and magic != "":
                magic_headers.append(magic)
            if size not in file_sizes:
                file_sizes.append(size)

        # If different magic headers are less than 5
        if len(magic_headers) <= 5:
            magic_string = " or ".join(getUintString(h) for h in magic_headers)
            if " or " in magic_string:
                condition = "( {0} )".format(magic_string)
            else:
                condition = "{0}".format(magic_string)

        # Biggest size multiplied with maxsize_multiplier
        if not args.nofilesize:
            if condition != "":
                condition = "{0} and {1}".format(condition, getFileRange(max(file_sizes)))
            else:
                condition = "{0}".format(getFileRange(max(file_sizes)))

    except Exception, e:
        if args.debug:
            traceback.print_exc()
            exit(1)
        print "[E] ERROR while generating general condition - check the global rule and remove it if it's faulty"

    return condition


def createRules(file_strings, super_rules, file_info, inverse_stats):

    # Write to file ---------------------------------------------------
    if args.o:
        try:
            fh = open(args.o, 'w')
        except Exception, e:
            traceback.print_exc()

    # General Info
    general_info = "/*\n"
    general_info += "\tYara Rule Set\n"
    general_info += "\tAuthor: {0}\n".format(args.a)
    general_info += "\tDate: {0}\n".format(getTimestampBasic())
    general_info += "\tIdentifier: {0}\n".format(os.path.basename(args.m))
    general_info += "*/\n\n"

    fh.write(general_info)

    # GLOBAL RULES ----------------------------------------------------
    if not args.noglobal:

        condition = generateGeneralCondition(file_info)

        # Global Rule
        if condition != "":
            global_rule = "/* Global Rule -------------------------------------------------------------- */\n"
            global_rule += "/* Will be evaluated first, speeds up scanning process, remove at will */\n\n"
            global_rule += "global private rule gen_characteristics {\n"
            global_rule += "\tcondition:\n"
            global_rule += "\t\t{0}\n".format(condition)
            global_rule += "}\n\n"

            # Write rule
            if args.o:
                fh.write(global_rule)

    # General vars
    rules = ""
    printed_rules = {}
    rule_count = 0
    inverse_rule_count = 0
    super_rule_count = 0

    if not args.inverse:
        # PROCESS SIMPLE RULES ----------------------------------------------------
        print "[+] Generating simple rules ..."
        # Apply intelligent filters
        print "[-] Applying intelligent filters to string findings ..."
        for filePath in file_strings:

            print "[-] Filtering string set for %s ..." % filePath

            # Replace the original string set with the filtered one
            string_set = file_strings[filePath]
            file_strings[filePath] = []
            file_strings[filePath] = filterStringSet(string_set)
            if len(file_strings[filePath]) == 0:
                print "[W] Not enough high scoring strings to create a rule. (reduce -z param) FILE: %s" % filePath

        # GENERATE SIMPLE RULES -------------------------------------------
        fh.write("/* Rule Set ----------------------------------------------------------------- */\n\n")

        for filePath in file_strings:
            try:
                rule = ""
                (path, file) = os.path.split(filePath)
                # Prepare name
                fileBase = os.path.splitext(file)[0]
                # Create a clean new name
                cleanedName = fileBase
                # Adapt length of rule name
                if len(fileBase) < 8: # if name is too short add part from path
                    cleanedName = path.split('\\')[-1:][0] + "_" + cleanedName
                # File name starts with a number
                if re.search(r'^[0-9]', cleanedName):
                    cleanedName = "sig_" + cleanedName
                # clean name from all characters that would cause errors
                cleanedName = re.sub('[^\w]', r'_', cleanedName)
                # Check if already printed
                if cleanedName in printed_rules:
                    printed_rules[cleanedName] += 1
                    cleanedName = cleanedName + "_" + str(printed_rules[cleanedName])
                else:
                    printed_rules[cleanedName] = 1

                # Print rule title ----------------------------------------
                rule += "rule %s {\n" % cleanedName

                # Meta data -----------------------------------------------
                rule += "\tmeta:\n"
                rule += "\t\tdescription = \"%s - file %s\"\n" % ( args.p, file )
                rule += "\t\tauthor = \"%s\"\n" % args.a
                rule += "\t\treference = \"%s\"\n" % args.r
                rule += "\t\tdate = \"%s\"\n" % getTimestampBasic()
                rule += "\t\thash = \"%s\"\n" % file_info[filePath]["hash"]
                rule += "\tstrings:\n"

                # Get the strings -----------------------------------------
                # Rule String generation
                rule_strings = getRuleStrings(file_strings[filePath])
                rule += rule_strings

                # Condition -----------------------------------------------
                condition = "all of them"

                # Filesize
                if not args.nofilesize:
                    condition = "{0} and {1}".format(getFileRange(file_info[filePath]["size"]), condition)

                # Magic
                if file_info[filePath]["magic"] != "":
                    uint_string = getUintString(file_info[filePath]["magic"])
                    condition = "{0} and {1}".format(uint_string, condition)

                rule += "\tcondition:\n"
                rule += "\t\t%s\n" % condition
                rule += "}\n\n"

                # print rule
                # Add to rules string
                rules += rule
                # Try to write rule to file
                if args.o:
                    fh.write(rule)
                rule_count += 1
            except Exception, e:
                traceback.print_exc()

    # PROCESS INVERSE RULES -------------------------------------------
    # print inverse_stats.keys()
    if args.inverse:
        print "[+] Generating inverse rules ..."
        inverse_rules = ""
        # Apply intelligent filters ---------------------------------------
        print "[+] Applying intelligent filters to string findings ..."
        for fileName in inverse_stats:

            print "[-] Filtering string set for %s ..." % fileName

            # Replace the original string set with the filtered one
            string_set = inverse_stats[fileName]
            inverse_stats[fileName] = []
            inverse_stats[fileName] = filterStringSet(string_set)

        # GENERATE INVERSE RULES -------------------------------------------
        fh.write("/* Inverse Rules ------------------------------------------------------------- */\n\n")

        for fileName in inverse_stats:
            try:
                rule = ""
                # Create a clean new name
                cleanedName = fileName.replace(".", "_")
                # Add ANOMALY
                cleanedName += "_ANOMALY"
                # File name starts with a number
                if re.search(r'^[0-9]', cleanedName):
                    cleanedName = "sig_" + cleanedName
                # clean name from all characters that would cause errors
                cleanedName = re.sub('[^\w]', r'_', cleanedName)
                # Check if already printed
                if cleanedName in printed_rules:
                    printed_rules[cleanedName] += 1
                    cleanedName = cleanedName + "_" + str(printed_rules[cleanedName])
                else:
                    printed_rules[cleanedName] = 1

                # Print rule title ----------------------------------------
                rule += "rule %s {\n" % cleanedName

                # Meta data -----------------------------------------------
                rule += "\tmeta:\n"
                rule += "\t\tdescription = \"%s for anomaly detection - file %s\"\n" % ( args.p, fileName )
                rule += "\t\tauthor = \"%s\"\n" % args.a
                rule += "\t\treference = \"%s\"\n" % args.r
                rule += "\t\tdate = \"%s\"\n" % getTimestampBasic()
                for i, hash in enumerate(file_info[fileName]["hashes"]):
                    rule += "\t\thash%s = \"%s\"\n" % (str(i+1), hash)

                rule += "\tstrings:\n"

                # Get the strings -----------------------------------------
                # Rule String generation
                rule_strings = getRuleStrings(inverse_stats[fileName])
                rule += rule_strings

                # Condition -----------------------------------------------
                folderNames = ""
                if not args.nodirname:
                    folderNames += "and ( filepath matches /"
                    folderNames += "$/ or filepath matches /".join(file_info[fileName]["folder_names"])
                    folderNames += "$/ )"
                condition = "filename == \"%s\" %s and not ( all of them )" % (fileName, folderNames)

                rule += "\tcondition:\n"
                rule += "\t\t%s\n" % condition
                rule += "}\n\n"

                # print rule
                # Add to rules string
                inverse_rules += rule
                # Try to write rule to file
                if args.o:
                    fh.write(inverse_rules)
                inverse_rule_count += 1
            except Exception, e:
                traceback.print_exc()

    # GENERATE SUPER RULES --------------------------------------------
    if not args.nosuper and not args.inverse:

        fh.write("/* Super Rules ------------------------------------------------------------- */\n\n")
        super_rule_names = []

        print "[+] Generating super rules ..."
        printed_combi = {}
        for super_rule in super_rules:
            try:
                rule = ""
                # Prepare Name
                rule_name = ""
                file_list = []

                # Loop through files
                for filePath in super_rule["files"]:
                    (path, file) = os.path.split(filePath)
                    file_list.append(file)
                    # Prepare name
                    fileBase = os.path.splitext(file)[0]
                    # Create a clean new name
                    cleanedName = fileBase
                    # Append it to the full name
                    rule_name += "_" + cleanedName

                # Shorten rule name
                rule_name = rule_name[:124]
                # Add count if rule name already taken
                if rule_name not in super_rule_names:
                    rule_name = "%s_%s" % (rule_name, super_rule_count)
                super_rule_names.append(rule_name)

                # Create a list of files
                file_listing = ", ".join(file_list)

                # File name starts with a number
                if re.search(r'^[0-9]', rule_name):
                    rule_name = "sig_" + rule_name
                # clean name from all characters that would cause errors
                rule_name = re.sub('[^\w]', r'_', rule_name)
                # Check if already printed
                if rule_name in printed_rules:
                    printed_combi[rule_name] += 1
                    rule_name = rule_name + "_" + str(printed_combi[rule_name])
                else:
                    printed_combi[rule_name] = 1

                # Print rule title
                rule += "rule %s {\n" % rule_name
                rule += "\tmeta:\n"
                rule += "\t\tdescription = \"%s - from files %s\"\n" % ( args.p, file_listing )
                rule += "\t\tauthor = \"%s\"\n" % args.a
                rule += "\t\treference = \"%s\"\n" % args.r
                rule += "\t\tdate = \"%s\"\n" % getTimestampBasic()
                rule += "\t\tsuper_rule = 1\n"
                for i, filePath in enumerate(super_rule["files"]):
                    rule += "\t\thash%s = \"%s\"\n" % (str(i+1), file_info[filePath]["hash"])

                rule += "\tstrings:\n"

                # Adding the strings
                rule_strings = getRuleStrings(super_rule["strings"])
                rule += rule_strings

                # Condition -------------------------------------------
                # Default
                condition = "all of them"
                # Evaluate the general characteristics
                file_info_super = {}
                for filePath in super_rule["files"]:
                    file_info_super[filePath] = file_info[filePath]
                condition_extra = generateGeneralCondition(file_info_super)
                if condition_extra != "":
                    condition = "{0} and {1}".format(condition_extra, condition)

                rule += "\tcondition:\n"
                rule += "\t\t{0}\n".format(condition)
                rule += "}\n"

                # print rule
                # Add to rules string
                rules += rule
                # Try to write rule to file
                if args.o:
                    fh.write(rule)
                super_rule_count += 1
            except Exception, e:
                traceback.print_exc()

    # Close the rules file --------------------------------------------
    if args.o:
        try:
            fh.close()
        except Exception, e:
            traceback.print_exc()

    # Print rules to command line -------------------------------------
    if args.debug:
        print rules

    return ( rule_count, inverse_rule_count, super_rule_count )


def getRuleStrings(elements):

    rule_strings = ""

    # Adding the strings --------------------------------------
    for i, string in enumerate(elements):

        # Collect the data
        enc = " ascii"
        base64comment = ""
        reversedComment = ""
        fullword = ""
        pestudio_comment = ""
        score_comment = ""
        goodware_comment = ""

        if string in good_strings:
            goodware_comment = " /* Goodware String - occured %s times */" % ( good_strings[string] )

        if string in stringScores:
            if args.score:
                score_comment += " /* score: '%.2f' */" % stringScores[string]
        else:
            print "NO SCORE: %s" % string

        if string[:8] == "UTF16LE:":
            string = string[8:]
            enc = " wide"
        if string in base64strings:
            base64comment = " /* base64 encoded string '%s' */" % base64strings[string]
        if string in pestudioMarker:
            pestudio_comment = " /* PEStudio Blacklist: %s */" % pestudioMarker[string]
        if string in reversedStrings:
            reversedComment = " /* reversed goodware string '%s' */" % reversedStrings[string]

        # Checking string length
        is_fullword = True
        if len(string) > args.s:
            # cut string
            string = string[:args.s].rstrip("\\")
            # not fullword anymore
            is_fullword = False
        # Show as fullword
        if is_fullword:
            fullword = " fullword"

        # No compose the rule line
        rule_strings += "\t\t$s%s = \"%s\"%s%s%s%s%s%s%s\n" % ( str(i), string, fullword, enc, base64comment, reversedComment, pestudio_comment, score_comment, goodware_comment )

        # If too many string definitions found - cut it at the
        # count defined via command line param -rc
        if i > int(args.rc):
            break

    return rule_strings


def initializePEStudioStrings():
    pestudio_strings = {}

    tree = etree.parse('strings.xml')

    pestudio_strings["strings"] = tree.findall(".//string")
    pestudio_strings["av"] = tree.findall(".//av")
    pestudio_strings["folder"] = tree.findall(".//folder")
    pestudio_strings["os"] = tree.findall(".//os")
    pestudio_strings["reg"] = tree.findall(".//reg")
    pestudio_strings["guid"] = tree.findall(".//guid")
    pestudio_strings["ssdl"] = tree.findall(".//ssdl")
    pestudio_strings["ext"] = tree.findall(".//ext")
    pestudio_strings["agent"] = tree.findall(".//agent")
    pestudio_strings["oid"] = tree.findall(".//oid")
    pestudio_strings["priv"] = tree.findall(".//priv")

    # Obsolete
    # for elem in string_elems:
    #    strings.append(elem.text)

    return pestudio_strings


def initializeBayesFilter(good_strings):

    # BayesTrainer
    stringTrainer = Trainer(tokenizer)

    # Train with goodware strings
    #print "[-] Training filter with good strings from good string db that occur more than 10 times"
    #for string in good_strings:
    #    number_of_occurrences = good_strings[string]
    #    if number_of_occurrences > 10 and len(string) > 10:
    #        # print string
    #        stringTrainer.train(string, "string")

    # Read the sample files and train the algorithm
    print "[-] Training filter with good strings from ./lib/good.txt"
    with open("./lib/good.txt", "r") as fh_goodstrings:
        for line in fh_goodstrings:
            # print line.rstrip("\n")
            stringTrainer.train(line.rstrip("\n"), "string")
            modified_line = re.sub(r'(\\\\|\/|\-|\.|\_)', ' ', line)
            stringTrainer.train(modified_line, "string")
    #print "[-] Training filter with garbage strings from ./lib/bad.txt"
    #with open("./lib/bad.txt", "r") as fh_badstrings:
    #    for line in fh_badstrings:
    #        # print line.rstrip("\n")
    #        stringTrainer.train(line.rstrip("\n"), "garbage")
    return stringTrainer


def getPEStudioScore(string):
    for type in pestudio_strings:
        for elem in pestudio_strings[type]:
            # Full match
            if elem.text.lower() in string.lower():
                # Exclude the "extension" black list for now
                if type != "ext":
                    return 5, type
    return 0, ""


def getMagic(filePath):
    magic = ""
    try:
        with open(filePath, 'rb') as f:
            magic = f.read(2)
    except Exception, e:
        pass
    finally:
        return magic


def getUintString(magic):
    if len(magic) == 2:
        return "uint16(0) == 0x{1}{0}".format(magic[0].encode('hex'), magic[1].encode('hex'))
    if len(magic) == 4:
        return "uint32(0) == 0x{3}{2}{1}{0}".format(magic[0].encode('hex'), magic[1].encode('hex'), magic[2].encode('hex'), magic[3].encode('hex'))
    return ""


def getFileRange(size):
    size_string = ""
    try:
        # max sample size - args.fm times the original size
        max_size = size * args.fm
        if max_size < 1024:
            max_size = 1024
        size_string = "filesize < {0}KB".format( max_size / 1024 )
    except Exception, e:
        pass
    finally:
        return size_string


def getTimestampBasic(date_obj=None):
    if not date_obj:
        date_obj = datetime.datetime.now()
    date_str = date_obj.strftime("%Y-%m-%d")
    return date_str


def isAsciiChar(b, padding_allowed=False):
    if padding_allowed:
        if ( ord(b)<127 and ord(b)>31 ) or ord(b) == 0 :
            return 1
    else:
        if ord(b)<127 and ord(b)>31 :
            return 1
    return 0


def isAsciiString(string, padding_allowed=False):
    for b in string:
        if padding_allowed:
            if not ( ( ord(b)<127 and ord(b)>31 ) or ord(b) == 0 ):
                return 0
        else:
            if not ( ord(b)<127 and ord(b)>31 ):
                return 0
    return 1


def isBase64(s):
    return (len(s) % 4 == 0) and re.match('^[A-Za-z0-9+/]+[=]{0,2}$', s)


def save(object, filename, bin = 1):
    file = open(filename, 'wb')
    file.write(pickle.dumps(object, bin))
    file.close()


def load(filename):
    file = gzip.GzipFile(filename, 'rb')
    buffer = ""
    while 1:
        data = file.read()
        if data == "":
            break
        buffer += data
    object = pickle.loads(buffer)
    del(buffer)
    file.close()
    return object


def printWelcome():
    print "###############################################################################"
    print "                        ______"
    print "      __  ______ ______/ ____/__  ____"
    print "     / / / / __ `/ ___/ / __/ _ \/ __ \\"
    print "    / /_/ / /_/ / /  / /_/ /  __/ / / /"
    print "    \__, /\__,_/_/   \____/\___/_/ /_/"
    print "   /____/"
    print "   "
    print "   Yara Rule Generator"
    print "   by Florian Roth"
    print "   July 2015"
    print "   Version 0.14.2"
    print "   "
    print "###############################################################################"


# MAIN ################################################################
if __name__ == '__main__':

    # Parse Arguments
    parser = argparse.ArgumentParser(description='yarGen')
    parser.add_argument('-m', help='Path to scan for malware')
    parser.add_argument('-g', help='Path to scan for goodware (dont use the database shipped with yaraGen)')
    parser.add_argument('-u', action='store_true', default=False, help='Update local goodware database (use with -g)')
    parser.add_argument('-c', action='store_true', default=False, help='Create new local goodware database (use with -g)')
    parser.add_argument('-o', help='Output rule file', metavar='output_rule_file', default='yargen_rules.yar')
    parser.add_argument('-p', help='Prefix for the rule description', metavar='prefix', default='Auto-generated rule')
    parser.add_argument('-a', help='Author Name', metavar='author', default='YarGen Rule Generator')
    parser.add_argument('-r', help='Reference', metavar='ref', default='not set')
    parser.add_argument('-l', help='Minimum string length to consider (default=8)', metavar='min-size', default=8)
    parser.add_argument('-z', help='Minimum score to consider (default=5)', metavar='min-score', default=5)
    parser.add_argument('-s', help='Maximum length to consider (default=128)', metavar='max-size', default=128)
    parser.add_argument('-rc', help='Maximum number of strings per rule (default=20, intelligent filtering will be applied)', metavar='maxstrings', default=20)
    parser.add_argument('-nr', action='store_true', default=False, help='Do not recursively scan directories')
    # parser.add_argument('-rm', action='store_true', default=False, help='Recursive scan of malware directories')
    # parser.add_argument('-rg', action='store_true', default=False, help='Recursive scan of goodware directories')
    parser.add_argument('-oe', action='store_true', default=False, help='Only scan executable extensions EXE, DLL, ASP, JSP, PHP, BIN, INFECTED')
    parser.add_argument('-fs', help='Max file size in MB to analyze (default=3)', metavar='size-in-MB', default=3)
    parser.add_argument('--score', help='Show the string scores as comments in the rules', action='store_true', default=False)
    parser.add_argument('--inverse', help='Show the string scores as comments in the rules', action='store_true', default=False)
    parser.add_argument('--nodirname', help='Don\'t use the folder name variable in inverse rules', action='store_true', default=False)
    parser.add_argument('--noscorefilter', help='Don\'t filter strings based on score (default in \'inverse\' mode)', action='store_true', default=False)
    parser.add_argument('--excludegood', help='Force the exclude all goodware strings', action='store_true', default=False)
    parser.add_argument('--nosimple', help='Skip simple rule creation for files included in super rules', action='store_true', default=False)
    parser.add_argument('--nomagic', help='Don\'t include the magic header condition statement', action='store_true', default=False)
    parser.add_argument('--nofilesize', help='Don\'t include the filesize condition statement', action='store_true', default=False)
    parser.add_argument('-fm', help='Multiplier for the maximum \'filesize\' condition (default: 5)', default=5)
    parser.add_argument('--noglobal', help='Don\'t create global rules', action='store_true', default=False)
    parser.add_argument('--nosuper', action='store_true', default=False, help='Don\'t try to create super rules that match against various files')
    parser.add_argument('--debug', action='store_true', default=False, help='Debug output')

    args = parser.parse_args()

    # Print Welcome
    printWelcome()

    if not os.path.isfile("good-strings.db"):
        print "[E] Please unzip the shipped good-strings.db database. Github does not allow files larger than 100MB. I'll think about a more comfortable way in the near future."
        sys.exit(1)

    # Read PEStudio string list
    pestudio_strings = {}
    pestudio_available = False
    if os.path.exists("strings.xml") and lxml_available:
        print "[+] Processing PEStudio strings ..."
        pestudio_strings = initializePEStudioStrings()
        pestudio_available = True
    else:
        if lxml_available:
            print "\nTo improve the analysis process please download the awesome PEStudio tool by marc @ochsenmeier from http://winitor.com and place the file 'strings.xml' in the yarGen program directory.\n"
            time.sleep(5)

    # Ignore File Type on Malware Scan
    onlyRelevantExtensions = False
    if args.oe:
        onlyRelevantExtensions = True

    # Scan goodware files
    if args.g:
        print "[+] Processing goodware files ..."
        good_strings = parseGoodDir(args.g, args.nr, True)

        # Update existing Pickle
        if args.u:
            print "[+] Updating local database ..."
            try:
                good_pickle = load("good-strings.db")
                print "Old database entries: %s" % len(good_pickle)
                # new_good = Counter()
                good_pickle.update(good_strings)
                # good_pickle = new_good
                print "New database entries: %s" % len(good_pickle)
                save(good_pickle, "good-strings.db")

            except Exception, e:
                traceback.print_exc()

        # Create new Pickle
        if args.c:
            print "[+] Creating local database ..."
            try:
                if os.path.isfile("good-strings.db"):
                    os.remove("good-strings.db")

                good_pickle = Counter()
                good_pickle = good_strings
                # print good_strings

                save(good_pickle, "good-strings.db")

                print "New database with %s entries created." % len(good_pickle)
            except Exception, e:
                traceback.print_exc()

    # Use the Goodware String Database
    else:
        print "[+] Reading goodware strings from database 'good-strings.db' ..."
        print "    (This could take some time and uses up to 2 GB of RAM)"
        try:
            good_pickle = load("good-strings.db")
            # print good_pickle
            good_strings = good_pickle
        except Exception, e:
            traceback.print_exc()

    # Initialize Bayes Trainer (we will use the goodware string database for this)
    print "[+] Initializing Bayes Filter ..."
    stringTrainer = initializeBayesFilter(good_strings)

    # If malware directory given
    if args.m:
        # Scan malware files
        print "[+] Processing malware files ..."

        # Special strings
        base64strings = {}
        reversedStrings = {}
        pestudioMarker = {}
        stringScores = {}

        # Extract all information
        sample_string_stats, file_info = parseSampleDir(args.m, args.nr, True, onlyRelevantExtensions)

        # Evaluate Strings
        (file_strings, combinations, super_rules, inverse_stats) = sampleStringEvaluation(sample_string_stats, good_strings, file_info)

        # Create Rule Files
        (rule_count, inverse_rule_count, super_rule_count) = createRules(file_strings, super_rules, file_info, inverse_stats)

        if args.inverse:
            print "[=] Generated %s INVERSE rules." % str(inverse_rule_count)
        else:
            print "[=] Generated %s SIMPLE rules." % str(rule_count)
            if not args.nosuper:
                print "[=] Generated %s SUPER rules." % str(super_rule_count)
            print "[=] All rules written to %s" % args.o
