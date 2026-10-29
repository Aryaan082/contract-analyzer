// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract VoucherDesk {
    address public issuer;
    mapping(address => uint256) public credit;
    mapping(bytes32 => bool) public spent;

    constructor(address issuer_) {
        issuer = issuer_;
    }

    function redeem(address to, uint256 amount, uint8 v, bytes32 r, bytes32 s) external {
        bytes32 digest = keccak256(abi.encodePacked(address(this), block.chainid, to, amount));
        require(!spent[digest], "spent");
        spent[digest] = true;
        address signer = ecrecover(digest, v, r, s);
        require(signer != address(0) && signer == issuer, "bad signature");
        credit[to] += amount;
    }
}
