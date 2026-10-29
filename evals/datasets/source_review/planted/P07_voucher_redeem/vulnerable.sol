// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract VoucherDesk {
    address public issuer;
    mapping(address => uint256) public credit;

    constructor(address issuer_) {
        issuer = issuer_;
    }

    function redeem(address to, uint256 amount, uint8 v, bytes32 r, bytes32 s) external {
        bytes32 digest = keccak256(abi.encodePacked(to, amount));
        address signer = ecrecover(digest, v, r, s);
        require(signer == issuer, "bad signature");
        credit[to] += amount;
    }
}
